# Unit `routes`: send each item the easiest way the payer accepts (progress)

Brief: `plans/briefs/2026-11-28_routes.md` (with `plans/briefs/2026-11-26_common.md`, CLAUDE.md, plans/HANDOFF.md).
Branch: `wip/routes`, from `origin/claude/ecstatic-fermi-orfcgo` at b170aed, tip 8feee1b merged. Invented data only; no
real data was copied or read by this unit.

## State

- [x] R1 `remitwatch/payer_routes.json`: 189 routes (payer group x request type x plan variant), 495 channels, 328
      sources, 82 corrections, checked 2026-11-28. Built by `python3 -I plans/tools/routes/build_payer_routes.py
      <research_raw.json> remitwatch/payer_routes.json` from the research file kept outside the repository
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
      styles and the `c` key (Copy all). "I sent it" posts the existing Sent with channel and reference.
- [x] Tests `tests/test_routes.py` (55) and `tests/routes_card_page.js` (Playwright on both hosts, 1400 and 390 px).
- [x] Screenshots (demo store, invented): `$S/tk/units/routes/shots/`, 10 items x 1400 and 390 px, panel and card.
- [x] Measurements and hand-trace on the demo store (below).
- [ ] Full suite python3 and 3.11.1 (`$S/fix/venv_sqlite340/bin/python`), test_docs with the private words: see the
      final report or `$S/tk/units/routes/runs/`.

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

## Open questions

- bill_secondary gets no card (the research has no row for billing a second payer; only the Medigap crossover row).
- The research covers Kentucky and Indiana; a plan of another state is shown its group's rows with the scope in words.
- A copy of routes.py was left at /routes_fixed.py (code only, no patient data); the safety check blocked removing it.
