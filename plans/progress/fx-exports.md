# fx-exports (fix wave F3b: the exports, the twins and G8): progress

Brief: `plans/briefs/2026-11-26_fixwave.md`, section "F3b the exports, the twins and G8" (and the common brief
`plans/briefs/2026-11-26_common.md`). Branch `wip/fx-exports`, started from the integration tip bc29767 (fix7d merged).
Private folder: `$S/tk/units/fx-exports/` (mode 700; scripts `measure.sh`, `snap2.py`, `compare.py`, `a17cmp.py`, `ht.py`
(hand-trace: raw rows of a finding's bill, dates as days from the visit, ids hashed), `breakfix.py`, `breakfix2.py`,
`claim_audit_a17.py` + `a17/payer_windows.json` taken from origin/wip/a17).

Functions this fixer owns (detect.py): `run_ema_open_claims`, `_ema_check_open_claims`, `_ema_claims_found`,
`_send_day_twins`, `run_ema_report_claims`, and the new helpers they call: `_export_patients`, `_note_resent_bill`,
`_claim_content`, `_unanswered_before_send`, `_bills_with_lines`, `_g8_one_service`, `_exports_yield_to_newer_bills`.
Small edit outside: `_save_ema_wiring` (no fixer owns it) writes the feed date as the unseen meta's newest_835.

## State (updated 2026-11-26 23:15 UTC)

- Every finding and residual in scope is fixed but one residual (export claims dated only by the visit day get no
  send-day twins: left, see below). `tests/test_fx_exports.py`: 18 tests, 42 cases (both store kinds), green; every fix
  broken on purpose and restored exactly (sha256 equal after `breakfix.py`).
- The world measurement found one regression of this branch's answers-3 fix and it is fixed (39c5121, 3df7489): a token
  line ($0.01 J3590) left the claim's content, so the content bridge found no EMA claim and 2 export claims EMA shows
  paid were listed. The same gap was already in the 837 pass (15 open items A17 calls already done); `_ema_claims_found`
  now matches every claim's whole content, so both passes are fixed.
- Merges: the integration tip 5eb528e is merged (4ba364b); the tip has not moved since (checked 23:00 UTC).

## Findings and residuals: status

| Item | Status | Test (tests/test_fx_exports.py) |
|---|---|---|
| answers-1, exports' side | fixed: `_exports_yield_to_newer_bills` (run_ema_report_claims, after the 837 pass, with or without EMA's reports): an exports' item yields each visit (date, code, part) a newer bill only the 837s know carries, for the same patient by name (`_patient_keys`: never the subscriber id alone); its other services stay, sized again; work follows through `_heirs` | test_a_visit_the_exports_chase_yields_to_a_newer_bill_only_the_837s_know, test_two_people_on_one_subscriber_id_seen_the_same_day_stay_two_services, test_an_export_claim_keeps_the_services_the_newer_bill_does_not_carry, test_two_parts_of_a_service_on_two_bills_are_two_services |
| answers-2 | fixed: `_ema_check_open_claims(final, newest)` takes the feed date; the unseen meta's newest_835 is the feed date | test_an_export_claim_no_source_can_check_waits_whatever_stray_remittance_is_newer |
| answers-3, exports' half | fixed: live lines leave out a line billed $0 or under $1 (a line whose charge is not known is kept); a claim with nothing billed is not listed, counted (export_billed_nothing). The token lines stay in the claim's content (`_claim_content`) | test_a_line_billed_at_no_charge_is_nothing_owed_on_an_export_claim, test_a_line_billed_at_a_token_charge_still_tells_which_ema_claim_is_the_claims, test_a_claims_content_counts_each_token_line_once |
| answers-5, exports' half | fixed: a newer 837 submission drops the exports' claim only when the 837 pass takes it; for a bill answered as primary the claim stays, dated by the newer send, naming the newer claim (`_note_resent_bill`) | test_an_export_claim_whose_bill_went_out_again_stays_listed_when_nothing_else_chases_it |
| answers-7 | fixed: `_unanswered_before_send` (in `_ema_claims_found`): the only unanswered EMA claim of the claim's lines charged on or before its send, one to one (no other candidate claim takes it, no other untied bill on file has those lines: `_bills_with_lines`), that patient's when the patient is known | test_a_claim_charged_days_before_its_837_went_out_is_listed_once (with and without a token line), test_a_claim_two_unanswered_ema_claims_could_be_takes_neither, test_a_claim_with_another_bill_of_the_same_lines_on_file_takes_no_ema_claim, test_a_claim_whose_patient_is_named_takes_only_that_patients_ema_claim |
| residual: G8 three items for whole, 26 and TC; a part priced at the whole rate | fixed: `_g8_one_service` (run_ema_report_claims): the newest charge of one patient's service carries what it covers; each line priced by price_unanswered_lines (the part's rate, units, multiple-procedure share, never above its charge; $0 lines nothing) | test_production_lists_one_service_once_and_prices_its_parts_at_their_own_rates |
| residual: twins told apart by the patient's name key | fixed: `_send_day_twins`: when a claim has several twins, the patient's keyed name (`_patient_keys`) keeps the one twin that is the patient's; such a twin is the claim's EMA claim whatever else went out that day | test_send_day_twins_of_two_patients_are_told_apart_by_the_patients_name (with and without a token line) |
| residual: a print-batch duplicate made after the visit was paid stays listed | fixed: `_ema_check_open_claims`: only a claim that reached its payer and came back rejected is answered by postings after its send alone; a claim still in the print batch is answered by a payment that still stands on its visit, whatever its day | test_a_print_batch_duplicate_made_after_the_visit_was_paid_is_not_listed; tests/test_837_sends.py claim 89's expectation changed (a print-batch bill made after its visit was paid is that duplicate), every other assertion unchanged |
| residual: exports' one-visit rule by one EMA patient id | fixed: `_export_patients`: EMA patient ids whose keyed names meet are one patient for the one-visit rule | test_one_person_under_two_ema_patient_ids_is_one_patient_for_the_one_visit_rule |
| residual: export claims dated only by the visit day get no send-day twins | left, by design: a send-day twin needs the day the claim went out, and such a claim has none (no submission date, no bill date, no 837); the content bridge still applies to it. In the world the tip had 2 such items, both lines billed $0, which answers-3 now leaves out: none is left | (none) |

## Decisions taken (for the reviewers)

- answers-1: the reviewer's own scenario had no name for the patient anywhere (only the subscriber id). The brief and the
  verifier say never merge on the subscriber id alone (two people on one id seen the same day are two services), so the
  ported test adds the review's own identity evidence (an earlier remittance naming the member's patient). With nothing
  naming the patient, both items stay. F3a reads the 837's own patient loop (2000C/2010CA); once merged, the 837 can
  name the patient itself (see Needs).
- answers-3: a token line is nothing owed but part of the claim's content. Leaving it out of what finds EMA's claim made
  paid claims look unanswered (measured), so it is kept there and nowhere else.
- answers-5: the kept exports' item is dated by the newer send (the payer's answer is due from it), so it is not chased
  before the payer could answer the resend.
- answers-7: the new bridge never takes another patient's EMA claim: exactly one unanswered candidate, one to one against
  every untied bill on file with the same lines, and the patient's name when known.
- Print batch: a bill EMA made again after its visit was paid by the same payer, and left in the print batch, is a
  duplicate of a paid visit; test_837_sends claim 89 asserted the opposite and was changed with that reason.

## Needs from others

- F3a (837 pass): the reverse of answers-1 (an 837-only bill OLDER than an exports' bill of the same visit yields to the
  exports' item). wip/fx-837 has `_837_one_visit` with carriers; check at the merge that it covers an exports' carrier.
- F3a (x12_837.py, the 837 tables): the patient of each 837 claim as a keyed name the run can compare with
  `_patient_keys`. wip/fx-837 has `Detector._837_patients`; after both merges, `_exports_yield_to_newer_bills` should
  take the 837 item's patient from it (and the visit key from `detect._visit_line`, which adds the distinct-service
  modifiers). Failing test today (no name anywhere): the reviewer's
  since-answers-tests/test_answers_review.py::test_one_visit_in_the_exports_and_again_under_a_new_bill_only_the_837s_know_is_chased_once.
- F3a: keep `s837` on the 837 pass's claims (index 7: the claim key of the send its lines were read from), or pass
  `token_lines`: `_claim_content` reads the token lines from it. If the 837 pass stops leaving token lines out of its
  items, nothing is needed (the content then uses the items as they are; tested).
- At the merge with wip/fx-837 (asked by F3a; not done here because this branch's `_emit_open_claim` still sums
  `x[2]` and would fail on None): `run_ema_open_claims.live_items` gives None for a charge the exports do not give, in
  both places (`None if sig in unknown else sig[4]`, and the status fallback `None if (v[0], v[1]) in unknown else
  v[2]`); F3a's `_emit_open_claim` sums `x[2] or 0`. F3a's failing test once its stopgap goes:
  tests/test_workable.py::test_each_claim_is_chased_once_at_its_own_size_and_closed_ones_show_as_written_off. Also F3a's
  `_note_waiting(pcn, grp, sent, due, billed)` where run_ema_open_claims skips a claim not due yet (the function exists
  only on wip/fx-837).

## Next command

    cd $S/tk/wt/fx-exports && python3 -m pytest -q tests/test_fx_exports.py

## Measurements (private world copy: the live store image of 2026-11-24 with EMA's reports; today 2026-11-24)

Runs: the tip's code (bc29767) and this branch twice: 4ba364b (cec8036 plus the integration tip 5eb528e) and 39c5121
(the token-line fix on top). Each on a fresh copy, then A17 (claim_audit_a17.py from origin/wip/a17, --as-of 2026-11-24).
The 3df7489 guard changes nothing on the world (the 837 pass still leaves token lines out of its items).

Open never-answered items, tip -> branch (39c5121):

| Signal | Listed by | Tip | Branch |
|---|---|---|---|
| NO_ANSWER | 837 pass | 767, $145,152.38 | 750, $143,497.70 |
| NO_ANSWER | weekly exports | 45, $6,364.54 | 43, $6,305.61 |
| NO_ANSWER | Production (G8) | 10, $1,352.02 | 10, $1,198.40 |
| REJECTED_PRE_PAYER | 837 pass | 10, $1,893.34 | 11, $1,904.78 |
| REJECTED_PRE_PAYER | weekly exports | 35, $6,048.81 | 35, $6,048.81 |
| REJECTED_PRE_PAYER | Production | 6, $2,385.77 | 5, $2,374.33 |

All open items 10,755 ($3,908,321.61) -> 10,736 ($3,906,454.38): 19 items left the list ($1,713.61), 3 Production items
were sized again (-$153.62), 1 claim listed twice became one item.
- By payer group, the items that left: UnitedHealthcare Medicare Advantage 11 ($1,226.80), Anthem / BCBS 4 ($253.41),
  Cigna 3 ($221.96), Humana Medicare Advantage 1 ($11.44, a Production duplicate). Sized again: Anthem / BCBS 2
  (-$119.62), Optum (UHC) 1 (-$34.00).
- Why: 15 claims of the 837s and 1 more answered in EMA, found by their whole content once the token line counts
  (`_claim_content`); 2 export claims billed only at $0 (answers-3); 1 Production rejected item whose claim the 837
  pass now carries (one claim, one item; that 837 item turned from NO_ANSWER to REJECTED_PRE_PAYER, fix and resubmit);
  3 Production items priced line by line (`_g8_one_service`: capped at their charge, a drug line valued at the payer's
  share of charges).
- Run counts: 837_answered_in_ema 4,891 -> 4,926; 837_no_answer 777 -> 761; export_billed_nothing 10; 
  unanswered_claims_one_service 2 (both protos were not listed on the tip either); no EMA step failed. answers-1's
  yield, answers-5's resend and the two-charts rule found no case in this world (no count).
- The first branch run (4ba364b) listed 2 export claims EMA shows paid (a $0.01 J3590 left their content): the
  regression fixed in 39c5121; the second run lists neither.

A17's classes, tip -> branch: ALREADY_DONE|open 2,375 ($664,542.82) -> 2,358 ($662,961.15), -17 and -$1,581.67;
CORRECT|open 4,758 -> 4,757 (-$120.50: a Cigna claim EMA shows answered, see the hand trace); UNDECIDED|open 1,118 ->
1,117 (-$165.06: the Production duplicate and the 3 sized again). Money left on the table unchanged.

Time and memory: detection 216.4 s on the tip, 279.5 s and 282.1 s on the branch runs, peak 1,055 MB in all three. The
machine was busier during the branch runs (the unchanged ema_turnaround step took 5.9 s on the tip run, 9.8 s and 9.1 s
on the branch runs). A timed pair run back to back on a loaded machine (`time_run.py`: wall time per Detector method,
nested calls inside their callers; tip 433.5 s, branch 486.2 s in all, the unchanged save step 89.2 s and 93.6 s) puts
this branch's cost at about 5 s: the three passes it touches took 102.6 s on the tip and 107.4 s on the branch
(run_ema_open_claims 25.0 -> 22.9, run_837_open_claims 63.6 -> 69.9, run_ema_report_claims 14.0 -> 14.6); the new steps
themselves: _claim_content 1.8 s (two calls), _unanswered_before_send 0.5 s, _export_patients 0.2 s, _g8_one_service and
_exports_yield_to_newer_bills under 0.1 s. Peak memory unchanged.

## Hand trace (27 moved items, raw rows: 835s, 837 sends and lines, EMA's export rows and report lines; ht.py, ht2.py)

- Right (25):
  - 2 export claims whose only line is an 88305 billed $0 (the 26 and the TC): nothing owed (A17: ZERO_CHARGE).
  - 3 Production items sized again: two capped at their charge ($580.00 -> $414.21 on a $414.21 charge; $98.00 ->
    $64.00 on a $64.00 charge), one with its J3490 line valued at the payer's share of charges ($104.32 -> $150.49), as
    the 837 pass prices it.
  - 15 claims of the 837s that leave the list: each has exactly one EMA claim with every charged line, the token line
    included (J3590 $0.01, J3301 $0.84 to $0.90, J3357 $0.45, 99024 $0.01), the same payer (EMA's name for it), charged
    on or just before the send day, and the payer's payment or contractual answer posted after the send (12 paid, 3 a
    CO-45 answer with the rest moved to the patient). A17 agrees on 14 (ALREADY_DONE). The 15th it calls CORRECT
    (never answered): EMA shows Cigna's CO-45 on each line after the send and the patient paying the rest, balance $0;
    the payer answered, so this trace keeps the branch's verdict.
  - 1 Production rejected item that now rides on its 837 claim (the same EMA claim, found by content with its J3357
    token line), and that 837 item itself (NO_ANSWER -> REJECTED_PRE_PAYER: EMA calls the claim rejected).
  - 1 exports item that no longer lists its $0.01 J3590 line (still found in EMA, still $76.64).
  - 1 837 item whose EMA claim is now the one same-day twin with the member's keyed name (three Anthem twins of the
    same content charged the day before the send); EMA shows only a bare $0 row on it, so it stays listed.
- Wrong (2, fixed): the 2 export claims the first branch run listed (paid in EMA; a $0.01 J3590 left their content).
  Fixed in 39c5121; the second run does not list them.
- Unclear: none.
