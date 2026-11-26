# fx-exports (fix wave F3b: the exports, the twins and G8): progress

Brief: `plans/briefs/2026-11-26_fixwave.md`, section "F3b the exports, the twins and G8" (and the common brief
`plans/briefs/2026-11-26_common.md`). Branch `wip/fx-exports`, started from the integration tip bc29767 (fix7d merged).
Private folder: `$S/tk/units/fx-exports/` (mode 700; scripts `measure.sh`, `snap2.py`, `breakfix.py`, `breakfix2.py`,
`claim_audit_a17.py` + `a17/payer_windows.json` taken from origin/wip/a17).

Functions this fixer owns (detect.py): `run_ema_open_claims`, `_ema_check_open_claims`, `_ema_claims_found`,
`_send_day_twins`, `run_ema_report_claims`, and the new helpers they call: `_note_resent_bill`,
`_unanswered_before_send`, `_bills_with_lines`, `_g8_one_service`, `_exports_yield_to_newer_bills`.
Small edit outside: `_save_ema_wiring` (no fixer owns it) writes the feed date as the unseen meta's newest_835.

## State (updated 2026-11-26 23:00 UTC)

- Reviewer and verifier tests run against the tip (bc29767): every answers test still failed (36 failed, 8 passed; the
  8 are controls). The ones in this fixer's scope are ported into `tests/test_fx_exports.py` (26 tests, both store
  kinds), all green on the branch; each fix broken on purpose and restored (helper `breakfix.py`).
- Baseline measured on a private world copy with the tip's code (`snap_tip.sqlite`, `a17_tip/`, detection 216 s, peak
  1,055 MB). The branch run is next (after the twins residuals).

## Findings and residuals: status

| Item | Status | Test (tests/test_fx_exports.py) |
|---|---|---|
| answers-1, exports' side | fixed: `_exports_yield_to_newer_bills` (run_ema_report_claims, after the 837 pass, with or without EMA's reports): an exports' item yields each visit (date, code, part) a newer bill only the 837s know carries, for the same patient by name (`_patient_keys`: never the subscriber id alone); its other services stay, sized again; work follows through `_heirs` | test_a_visit_the_exports_chase_yields_to_a_newer_bill_only_the_837s_know, test_two_people_on_one_subscriber_id_seen_the_same_day_stay_two_services, test_an_export_claim_keeps_the_services_the_newer_bill_does_not_carry, test_two_parts_of_a_service_on_two_bills_are_two_services |
| answers-2 | fixed: `_ema_check_open_claims(final, newest)` takes the feed date; the unseen meta's newest_835 is the feed date | test_an_export_claim_no_source_can_check_waits_whatever_stray_remittance_is_newer |
| answers-3, exports' half | fixed: live lines leave out a line billed $0 or under $1 (a line whose charge is not known is kept); a claim with nothing billed is not listed, counted (export_billed_nothing) | test_a_line_billed_at_no_charge_is_nothing_owed_on_an_export_claim |
| answers-5, exports' half | fixed: a newer 837 submission drops the exports' claim only when the 837 pass takes it; for a bill answered as primary the claim stays, dated by the newer send, naming the newer claim (`_note_resent_bill`) | test_an_export_claim_whose_bill_went_out_again_stays_listed_when_nothing_else_chases_it |
| answers-7 | fixed: `_unanswered_before_send` (in `_ema_claims_found`): the only unanswered EMA claim of the claim's lines charged on or before its send, one to one (no other candidate claim takes it, no other untied bill on file has those lines: `_bills_with_lines`), that patient's when the patient is known | test_a_claim_charged_days_before_its_837_went_out_is_listed_once, test_a_claim_two_unanswered_ema_claims_could_be_takes_neither, test_a_claim_with_another_bill_of_the_same_lines_on_file_takes_no_ema_claim, test_a_claim_whose_patient_is_named_takes_only_that_patients_ema_claim |
| residual: G8 three items for whole, 26 and TC; a part priced at the whole rate | fixed: `_g8_one_service` (run_ema_report_claims): the newest charge of one patient's service carries what it covers; each line priced by price_unanswered_lines (the part's rate, units, multiple-procedure share, never above its charge; $0 lines nothing) | test_production_lists_one_service_once_and_prices_its_parts_at_their_own_rates |
| residual: twins told apart by the patient's name key | open (next) | |
| residual: export claims dated by the visit day get no twins | open (next) | |
| residual: exports' one-visit rule by one EMA patient id | open (next) | |

## Decisions taken (for the reviewers)

- answers-1: the reviewer's own scenario had no name for the patient anywhere (only the subscriber id). The brief and the
  verifier say never merge on the subscriber id alone (two people on one id seen the same day are two services), so the
  ported test adds the review's own identity evidence (an earlier remittance naming the member's patient). With nothing
  naming the patient, both items stay. F3a reads the 837's own patient loop (2000C/2010CA); once it does, the 837 can
  name the patient itself (see Needs).
- answers-5: the kept exports' item is dated by the newer send (the payer's answer is due from it), so it is not chased
  before the payer could answer the resend.
- answers-7: the new bridge never takes another patient's EMA claim: exactly one unanswered candidate, one to one against
  every untied bill on file with the same lines, and the patient's name when known.

## Needs from others

- F3a (837 pass): the reverse of answers-1 (an 837-only bill OLDER than an exports' bill of the same visit should yield
  to the exports' item). The 837 pass runs after the exports' pass and can see `self._export_claims` and the exports'
  items; `_exports_yield_to_newer_bills` only moves exports' items.
- F3a (x12_837.py, the 837 tables): the patient of each 837 claim (the subscriber when SBR02 is 18 and there is no
  patient loop, else the 2010CA patient), as a keyed name the run can compare with `_patient_keys`; then
  `_exports_yield_to_newer_bills` can use it (the reviewer's no-name scenario becomes decidable).

## Next command

    cd $S/tk/wt/fx-exports && python3 -m pytest -q tests/test_fx_exports.py

## Measurements

- Tip baseline (bc29767 code, world 11-24, today 2026-11-24): open never-answered items 767 from the 837 pass
  ($145,152), 55 export NO_ANSWER ($7,717), 41 export REJECTED_PRE_PAYER ($8,435), 10 837 REJECTED ($1,893); 10 open G8
  items; 2 export items dated only by the visit day.
