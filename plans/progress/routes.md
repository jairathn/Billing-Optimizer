# Unit `routes`: send each item the easiest way the payer accepts (progress)

Brief: `plans/briefs/2026-11-28_routes.md` (with `plans/briefs/2026-11-26_common.md`, CLAUDE.md, plans/HANDOFF.md).
Branch: `wip/routes`, started from `origin/claude/ecstatic-fermi-orfcgo` at b170aed. Invented data only; no real data used.

## State (keep current)

- [x] R1 `remitwatch/payer_routes.json`: 189 routes (payer group x request type x plan variant), 495 channels, 328 sources,
      82 corrections, checked 2026-11-28. Built by `plans/tools/routes/build_payer_routes.py <research_raw.json> <out>` from
      the research file kept outside the repository (`$S/tk/routes/research_raw.json`); the verifier's corrections are the
      script's CORRECTIONS table (contradicted and outdated facts fixed or dropped, unverifiable ones marked
      `verified: false` with `unverified_why`, missing facts with sources added: HealthSpring, Anthem's clinical appeal and
      FEP, Ambetter's member appeal, VA Region 2, recoupment disputes...). The UHC Medicare Advantage rows the research wrote
      twice are kept once. Numbers written 866-290-4036; tests/test_docs.py passes with the private words.
- [x] R2 `remitwatch/routes.py`: `card(conn, finding_id, explain=..., today=..., show_names=...)`; `request_for` (route and
      facts to the request type; NO_CARD routes and claims that never reached the payer get none), `research_group` (app
      group to the research's, VA by payer id, HealthSpring, plans not covered), `pick` (plan variant: BlueCard, FEP,
      clinical, TriWest, MHS, TRICARE's three reviews, nonpar as alternatives), `classify`/`fill` (field labels to store
      values and PRACTICE_ settings; names only with REMITWATCH_SHOW_PHI), `_fax_view` (published vs confirmed number, the
      existing fax.number_for / no_fax / shared_group), `_copy_all`. New setting PRACTICE_PTAN (.env.example, both runbooks).
- [x] R3 the API: `api._explained` adds `route_card` (and `route_card_error`, logged with runlog.caught) to
      `/api/findings/{id}/explain` and `/api/findings/{id}?story=1`, on the server's store and Vercel's dashboard copy
      (the copy already holds findings, claims, lines, adjustments, ema_rows, finding_events, payer_fax: no serving change).
- [x] R4 the drawer card: features.js `xpRouteHtml` / `xpRouteBind` inside `xpBoxHtml` under the Next line; index.html
      styles (`.rc-*`) and the `c` key (Copy all) in the shortcuts sheet.
- [x] Tests `tests/test_routes.py` (48): table integrity and corrections, request types, payer and plan, fields, names off,
      fax rule, API on both hosts, a card that fails. Docs: README names routes.py.
- [x] Screenshots (demo store, invented): `$S/tk/units/routes/shots/` (6 items x 1400 and 390 px, panel and card), no page
      error, no sideways scroll, the c key copies.
- [ ] Measurements on the demo store, hand-trace of cards, suites (touched files, full python3, full 3.11.1), node checks.

## Next command

    cd $S/tk/wt/routes && env -u ANTHROPIC_BASE_URL python3 -m pytest -q tests/test_routes.py tests/test_story.py tests/test_ema_pages.py

## Numbers measured

(see below once measured)

## Open questions

- bill_secondary gets no card (the research has no row for billing a second payer; only the Medigap crossover row exists).
- The research covers Kentucky and Indiana; a plan of another state is shown its group's rows with the scope in words.
