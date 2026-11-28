# Unit `routes`: send each item the easiest way the payer accepts (progress)

Brief: `plans/briefs/2026-11-28_routes.md` (with `plans/briefs/2026-11-26_common.md`, CLAUDE.md, plans/HANDOFF.md).
Branch: `wip/routes`, started from `origin/claude/ecstatic-fermi-orfcgo` at b170aed. Invented data only; no real data used.

## State (keep current)

- [ ] R1 `remitwatch/payer_routes.json`: built by `plans/tools/routes/build_payer_routes.py` from the research file
      (`$S/tk/routes/research_raw.json`, outside the repository; never copied in) with the verifier's corrections applied
      in the script's CORRECTIONS table.
- [ ] R2 `remitwatch/routes.py`: request type, payer group match, ranked channels, prefilled fields, the fax rule.
- [ ] R3 the API: `explain` answer carries `route_card` (both hosts).
- [ ] R4 the drawer card (features.js, index.html).
- [ ] Tests `tests/test_routes.py`; docs (README module list, .env.example, runbooks).
- [ ] Screenshots at 1400 and 390 px for five items into `$S/tk/units/routes/shots/`.
- [ ] Suites: touched files, full python3, full 3.11.1 (`$S/fix/venv_sqlite340/bin/python`), node checks, test_docs with
      the private words.

## Next command

(see the list above)

## Numbers measured

(none yet)

## Open questions

(none yet)
