# Unit decisions: progress (the owner's decisions 2 and 3; decision 4 is another unit's)

Brief: `plans/briefs/2026-11-26_decisions.md` (sections "Decision 2" and "Decision 3" only). Rules:
`plans/briefs/2026-11-26_common.md`, `CLAUDE.md`, `plans/HANDOFF.md` section 3b.

Branch `wip/decisions`, based on the integration tip 1867f60 (nothing to merge on start).

## State (updated as work goes)

- [ ] Read the code paths (rule 8 in archive_rules.pr_group_denials; rule 3 units_split's `switched` closure; detect.py
      `_close_by_answer` (settled as secondary); states.py `_decide`/`route_of`; story.py `_next`/`_bill_patient_step`;
      api.py status routes and their guarantees; results.py attribution). In progress.
- [ ] Baseline on a private world copy (`$S/tk/units/decisions/`, mode 700).
- [ ] Decision 2 built with tests (both store kinds).
- [ ] Decision 3 built with tests (both store kinds).
- [ ] Measured after; 20+ moved items hand-traced from raw rows.
- [ ] Suites (touched files, then the full suite in python3 and 3.11.1).

## Numbers measured so far

(none yet)

## Open questions

(none yet)

## Next command

Read the code paths listed above.
