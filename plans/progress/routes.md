# Unit `routes`: send each item the easiest way the payer accepts (progress)

Brief: `plans/briefs/2026-11-28_routes.md` (with `plans/briefs/2026-11-26_common.md`, CLAUDE.md, plans/HANDOFF.md).
Branch: `wip/routes`, from `origin/claude/ecstatic-fermi-orfcgo` at b170aed, tip 8feee1b merged; the fix round merged
ed245a1. Invented data only; no real data was copied or read by this unit.

## State

- [x] R1 `remitwatch/payer_routes.json`: 189 routes (payer group x request type x plan variant), 495 channels, 328
      sources, 85 corrections (82 before the fix round), 58 gaps, 1 payer with one level of appeal, checked 2026-11-28.
      Built by `python3 -I plans/tools/routes/build_payer_routes.py <research_raw.json> remitwatch/payer_routes.json`
      from the research file kept outside the repository
      (`$S/tk/routes/research_raw.json`). The verifier's verdicts are the script's CORRECTIONS table: contradicted and
      outdated facts fixed or dropped, unverifiable ones kept with `verified: false` and `unverified_why`, low confidence
      never verified, missing facts with sources added (HealthSpring, Anthem's clinical appeal and FEP, Ambetter's member
      appeal, VA Region 2, recoupment disputes). Numbers written 866-290-4036; test_docs passes with the private words.
- [x] R2 `remitwatch/routes.py`: `card(conn, finding_id, explain=, today=, show_names=)`.
      `request_for`: route and facts to the request. Routes with no payer step get no card, and neither do claims that
      never reached the payer (NEVER_REACHED) or a second payer never billed (OTHER_PAYER). An Original Medicare appeal
      is a redetermination, or a reopening for a clerical fix. A recorded first denial gives the second level. call
      gives a claim status request, with the timely filing proof beside it near the first-filing limit (states.windows).
      `research_group` (VA by payer id, HealthSpring, plans not covered), `pick` (plan variants), `classify`/`fill`
      (field labels to store values and PRACTICE_ settings; names only with REMITWATCH_SHOW_PHI; an empty field says what
      to fill, worded per request). `_argument`: a reason field holds only the argument put to the payer.
      `_fax_view`: published vs confirmed number, through fax.number_for / no_fax / shared_group. A second-level card
      names its own limit and the first answer's date. `_copy_all`. New setting PRACTICE_PTAN (.env.example, both
      runbooks). Every except block logs through runlog.caught.
- [x] R3 `api._explained` adds `route_card` (and `route_card_error`, logged) to `/api/findings/{id}/explain` and
      `/api/findings/{id}?story=1`, on the server's store and Vercel's dashboard copy (no serving change needed).
- [x] R4 drawer card: features.js `xpRouteHtml` / `xpRouteBind` in `xpBoxHtml` under the Next line; index.html `.rc-*`
      styles and the `c` key (Copy all). "I sent it" posts the existing Sent with what was sent, the channel and the
      reference; "I asked the payer" is a line in the item's history (fix round).
- [x] Tests `tests/test_routes.py` (55) and `tests/routes_card_page.js` (Playwright on both hosts, 1400 and 390 px).
- [x] Screenshots (demo store, invented): `$S/tk/units/routes/shots/`, 10 items x 1400 and 390 px, panel and card.
- [x] Measurements and hand-trace on the demo store (below).
- [x] Suites (2026-11-28, at 2950594..6dd3e39): full python3 (3.13, SQLite 3.5x) 4368 passed, 29 skipped, 1 xfailed, 0
      failed; full 3.11.1 / SQLite 3.40.0: 4166 passed, 217 failed or errored, every one on the missing
      Connection.serialize of that build (allowed), none in test_routes; test_routes.py 55 passed on the newest
      libraries and on 3.12 / SQLite 3.45; test_docs with the private words 40 passed; both node checks pass.
- [x] Fix round of the two reviews (2026-11-28): 21 findings, all real, all fixed and tested (section below).
- [x] Fix round suites (2026-11-28, at 2fe57fd): full python3 (3.13, SQLite 3.53) 4423 passed, 29 skipped, 1 xfailed, 0
      failed (the 4368 before and the 55 new tests); full 3.11.1 / SQLite 3.40.0: 4221 passed, 217 failed or errored,
      every one on the missing Connection.serialize of that build (checked traceback by traceback), none in the route
      tests; tests/test_routes.py with tests/test_routes_review.py: 110 passed on python3, on the newest libraries
      (FastAPI 0.165, Starlette 1.10) and on 3.12 / SQLite 3.45; both node checks pass (79 and 143 checks); test_docs
      with the private words 40 passed. The reviewers' own folders: r1 11 passed, 5 failed and r2 21 passed, 1 failed,
      each failure a test that pinned the card before the fix (HealthSpring's second level, the old "Asked the payer"
      Sent, the old fax words, the old "Print the letter" label).

## Fix round (2026-11-28): the two reviews' 21 findings

Merged `origin/claude/ecstatic-fermi-orfcgo` at ed245a1 (HANDOFF only). Each finding was confirmed on invented data and
is real. Its test is ported into `tests/test_routes_review.py` (55 tests), with `tests/routes_card_render.js` (draws the
card from features.js in node) and `tests/routes_card_press.js` (Playwright: presses the card's buttons on a served
site). Then each fix was broken on purpose: 31 breaks, each failing its tests, each file restored with `git checkout`
(`git status` clean after). Three fixes no ported test pinned got a test of their own (a status call on a denial, a
second level with only the first level's number on file, a label no field rule knows).

Highs
- r1-2 "I asked the payer" posted a Sent: it parked an unanswered claim, and the payer's later "no" read as a turned-down
  first appeal (second level offered). Now it writes a line in the item's history (event kind `asked`, "Asked the
  payer", in api.EVENT_KINDS and the page's history words), which changes no status on either host. The card reads what
  was sent from the Sent's own note (`routes.sent_request`, the card's "Sent a first-level appeal by ..." words), and a
  status note ("Asked the payer", "Call or portal check") is no level of appeal.
- r1-3 HealthSpring rows rebuilt from its 2026 participating provider manual: one level of appeal (65 days, appeal fax
  and PO box) for medical necessity, clinical guideline, prior authorization and referral decisions; the claim
  reconsideration (180 days, Provider Payment Disputes, PO Box 1004, Nashville) for payment matters. A clinical denial
  takes the appeal row; after an upheld appeal the card says there is no second level (table key `single_level`).
- r1-4 The card never names a fax number the send does not use. RemitWatch's own send carries first-level appeals
  only (`FAX_SENDS`, api._payer_number) and Start the fax shows only when the confirmed appeal number is the one the
  payer publishes for this request. Every other request (second level, corrected claim, proof of timely filing,
  records, refund) is faxed by hand: Copy the fax number, with the words to confirm it first. A second level shows only
  a number saved for level 2, never the appeal number fax.number_for falls back to.
- r2-1 A downcode card copies the code billed (the payer's SVC06, a claim file or an EMA row), never the payer's lower
  code and never a code only the charge implies; the code the payer paid is its own fact.

Mediums
- r1-1 Medicare turned a reopening down: the card offers a redetermination of the first decision (a reopening's
  decision cannot be appealed, 42 CFR 405.980(a)(5); the QIC reviews only a redetermination, 405.960), by the first
  decision's clock (120 days from receipt of its remittance, presumed 5 days after its date). A redetermination turned
  down still goes to the QIC.
- r1-5 The Also fold and the "Another plan type?" list say whether the way was confirmed, show the fax words and the
  limit, as the card's own line does.
- r2-2 A refund card names the amount to send back (the next step's "Overpaid by").
- r2-3 A refund item from EMA's A/R (no claim key) fills the payer's claim by its EMA claim ID: the newest claim of the
  same payer, reversals and superseded files left out; never another payer's claim.
- r2-4 CGS's refund and reopening forms: "new value" and the MSP amounts are never the date of service.
- r2-6 A payment dispute carries what the payer should have paid, the shortfall and a reason sentence with both.
- r2-9, r2-10 An EOB to fetch, or a coordination of benefits record to fix, lists the portal or phone first; the
  276/277 status inquiry (it returns neither) stays among the other ways.
- r2-11 A mailed refund's main button prints a cover sheet (the claim's facts and what to enclose), not an appeal letter.
- r2-15 A claim total field holds the claim's total charge, not one line's.

Lows
- r1-6 The gaps keep no fact the verifier contradicted (Anthem Indiana guide dropped; Humana CLM05-3 and WPS waiver
  gaps rewritten); Humana's Medicare Advantage corrected-claim row cites Humana's own tip sheet first; Anthem's new
  clinical row no longer repeats the contradicted phone fact.
- r2-5 An "NPI or TIN" field takes whichever is set.
- r2-7 Every empty field says what goes there (new field rules and hints for every label in the table; a label no rule
  knows says so); the line's number comes from the remittance.
- r2-8 A field filled in part says what it still needs.
- r2-12 A payer the table does not cover still shows the claim's facts, Copy all and I sent it.
- r2-13 A clearinghouse way has a main button ("Send it from EMA").
- r2-14 The reference typed with I sent it or I asked the payer fills the next level's reference field.

Tests adapted from the reviewers' (they pinned the card before the fix): r1-2's browser test checks the history line is
posted and no Sent; r1-3's HealthSpring second level is now "there is none"; r1-4 and r1-5 check the new fax and chip
words; r2-11 the new label. In tests/test_routes.py: the Beneficiary Phone Number label fills `patient_contact` (names
switch), and the HealthSpring test follows its rows (a payment dispute takes the reconsideration, CO-50 the appeal).

Decision: r1-2 is a history line, not a Started (in_progress): a person's Started on an item already sent falls through
states.route_of to 'appeal' and would offer the first appeal again.

## Next command

    cd $S/tk/wt/routes && RW_RUNS=$S/tk/units/routes/runs bash plans/tools/shard.sh $PWD python3 py313 3

## Numbers measured (demo store, invented data, 358 items, 246 work now worth $75,049)

- 149 items have a card, all with a checked route (no fallback: every demo payer group is covered), 0 card errors.
- Work now: 144 items $17,631 have a card; 143 ($17,514, 99.3%) have a verified best way. No card by route: contract 12
  ($39,692), fees 22 ($6,176), decide 16 ($5,187), bill_patient 9 ($2,960), never reached the payer 10 ($2,337),
  bill_secondary 32 ($923), a second payer never billed with a person's start 1 ($143).
- By request and best way: claim status by clearinghouse 41 ($10,654), by portal 17 ($5,223); refund by portal 13
  ($1,840), by mail 3 ($575); reconsideration by portal 34 ($1,315), by mail 7 ($587); corrected claim by
  clearinghouse 15 ($1,188); Medicare reopening 10 ($1,014); redetermination 6 ($382); timely filing proof 1; second level 1.
- Reason fields: 85 filled, all with an appeal's argument; every other request leaves them empty with what to fill.

After the fix round (the same demo store, rebuilt from the same seed on this branch): 358 items, 149 with a card, 0 card
errors, work now unchanged (144 cards, $17,631; 143 with a verified best way, $17,514). 7 items whose next step fetches
the payer's EOB moved from the 276/277 status inquiry to the payer's portal (claim status by clearinghouse 41 to 34, by
portal 17 to 24). Fields filled: 235 of 391 before, 267 of 391 after. Every newly filled value was checked against the
store's rows: code billed 145, line number 10, code the payer paid 15, should have paid 9, underpaid by 9, refund amount
16; all agree. The 4 empty "Line (or ALL)" fields are refunds of an A/R credit with no remittance line: they say what
to fill.

## Hand-trace (10 cards, each against the item's 835 rows and the table row)

All ten: the request, payer group, best way (rank 1 accepted), not-accepted warnings, fields and facts (claim number,
member, date of service, code and modifiers, billed, paid, denial codes, remittance date) and source line agree.
Three defects found and fixed in routes.py, each with a failing test first, then broken and restored:

- A reopening, corrected claim, status request or refund filled its reason field with the biller's instruction.
- An unanswered claim's "proof beside it" keyed on a date 30 days past the first-filing limit.
- A second-level card showed the first level's date (Oct 2028), not its 90-day clock from the answer.

One label fixed: an EMA-only denial showed a "Remittance date".

## Not this unit's (for the owners of states.py / story.py)

- states.route_of: a SECONDARY_NOT_BILLED item a person started (rule person_started) falls to the last line, route
  'appeal', so its next step offers an appeal packet to the first payer (the card now steps aside).
- story.next_step for a Medicare timely filing denial says "Appeal to Medicare ... (redetermination)"; CMS takes no
  appeal of a timely filing denial (the table's m7 and the card route it to a reopening with proof).
- The work_answered item's date (next step) is the first level's window; the second level's clock is shorter.
- A NO_ANSWER next step says "it must reach the payer by <item date>" while its basis names the earlier first-filing limit.
- The drawer's outcome "The payer is working on it" (features.js xpOutcome) records a Sent ('appealed', "Call or portal
  check"), which parks an unanswered claim (states._follow) even past its first-filing limit. The route card reads that
  note as a status call (no level of appeal), but the parking is states.py's and the outcome button's.

## Open questions

- bill_secondary gets no card (the research has no row for billing a second payer; only the Medigap crossover row).
- The research covers Kentucky and Indiana; a plan of another state is shown its group's rows with the scope in words.
- A copy of routes.py was left at /routes_fixed.py (code only, no patient data); the safety check blocked removing it,
  and it is still there at the fix round: a person can delete it.
