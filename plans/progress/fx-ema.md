# fx-ema: F4, the EMA side and the bare $0 rule everywhere (progress)

Brief: `plans/briefs/2026-11-26_fixwave.md`, section "F4 the EMA side and the bare $0 rule everywhere". Rules:
`plans/briefs/2026-11-26_common.md`. Branch `wip/fx-ema` (worktree `$S/tk/wt/fx-ema`), from the integration tip 8c59195;
the tip 59f6822 (plans only since) merged at bffe448. No fixer's merge (F1, F3a, F3b) had reached the tip when this was
measured. Private folder `$S/tk/units/fx-ema/` (mode 700): `measure.sh`, `compare.py`, `breaks.py`, `patches/`,
`explore*.py`, the snapshots and the hand-trace file.

The F4 section names no review finding by id: its items are the fix7e-c residuals (plans/next_fixes_2026-11-24.md
sections 2 and 7, plans/briefs/2026-11-26_fix7e.md) and rule 4.2's measure (decisions unit). No reviewer test in
`$S/tk/review/since-*` reaches these functions (searched for zero_answer, turnaround, status_class, secondary_not_billed,
blindness, self_audit, _paid_share, fold_payer_policy, rarely_paid, work_evidence, Medicaid (other), C5/C6), so the
failing tests are written here: `tests/test_fx_ema.py`, 13 functions, 25 cases (both store kinds where the code runs on
both). All 25 fail on the tip (59f6822) and pass on the branch. Each fix was broken on purpose (`breaks.py`, 15 breaks):
every break fails its test, every file restored byte for byte.


## Fix round (2026-11-27, reviews fx-ema-r1 and fx-ema-r2): state
Branch `wip/fx-ema`; the integration tip merged at 214f729 (bf1f17b, F8's routed needs in the brief) and 3542374 (47b1ba0:
F1 fx-ledger merged; F3a and F3b not merged yet when this was written). The reviewers' tests are ported into
`tests/test_fx_ema.py` sections 8 (review round) and 9 (F8's routed needs), invented data, both store kinds: 46 new cases,
all failing on 68713a8 (the fix round's start) except the controls; 71 cases pass on the branch. Every fix was broken on
purpose and restored byte for byte (cmp), each break failing its own tests.

| Finding | Outcome | Where |
|---|---|---|
| r1-1, r2-1 (high) `_paid_share`'s loop rebinds `code` | fixed | detect `Detector._paid_share` (loop names s_grp, s_code, s_svc). The open never-pays test of tests/test_ema_wiring.py passes now |
| r2-2 a payment EMA shows taken back counts as paid | fixed | detect `_share_ema_paid` sums every row (no `ins_paid > 0` filter) by payer group and name; `_paid_share` nets the payer's rows |
| r2-3 rule 4.2 reads the catch-all as the state's Medicaid | fixed | `_paid_share` judges each EMA payment with `ema_checks._same_payer_named` |
| r1-5 the catch-all's research applies whatever the measure | fixed | `rarely_paid.applies`: an exact entry of 'Medicaid (other)' speaks only for a pair the run found rarely paid (as another spelling's does); C5's patterns (`ema_checks.ema_denials`) show the measure whenever it is known (fold_payer_policy already did) |
| r1-2, r2-6 C6 closes on payer + copay reaching half | fixed | `ema_checks._paid_with_patient` (used by `_outcome_of` and `paid_in_ema`): beside the patient's money the payer's payment must reach half of the share it owes (expected less what the patient paid); the five real copay closures keep closing (each payer paid 49% to 100% of its share), 9a116d67 stays listed ($279.59 < half of $585.21) |
| r1-3 a copay closure's proof shows the payer's money only | fixed (date left) | `paid_in_ema` returns `patient_cents`/`patient_date`; `detect._close_from_ema` adds a `patient_payment` proof row (small edit in a function no fixer owns, said in the commit). The row has no date: the store keeps none for patient money (item B, below) |
| r1-4 SNB lists a line the second payer adjudicated on its own check | fixed | `ema_checks.zero_answer_sql` (the shared predicate): a $0 row on a check of the payer that paid another line of the same EMA claim is that payer's answer. Decided: a lone bare $0 row (the reviewer's a1cb8473 family) stays no answer, as the brief's rule and the build round's A4 test say; it is listed with its row in EMA |
| r2-4 the self-audit reads a second payer's bare $0 row as its answer | fixed | `selfcheck._cov_secondary`: roles from `answer_row_sql` rows, plus a reason posted by a payer of another group |
| r2-5 the self-audit passes no plan fact | fixed | `selfcheck._Facts` (sc_svc gets contract_class and payer_id; EMA lines their plan), `_rule_facts`, `_cov_denials`, `_cov_short_pay`, `_cov_auto_writeoffs`, `_cov_unanswered` |
| r2-7 C7/C9 date a write-off by another payer's item | fixed | `ema_checks._finding_line_deadlines` keeps each item's payer group; `_own_deadline` reads the write-off's own payer's item, else the payer's rule |
| r2-8 an empty deadline stops C7 and C9 | fixed | `_finding_line_deadlines` skips a date that is empty or not a day |
| F8's needs (brief, "Routed from F8") | done | `ema_ledger.refresh` calls `ema_reports.learn_payer_aliases` first, and re-groups the stored lines, payments, checks (Part B's marks) and A/R rows (`_regroup_ar`) when the name rules differ from meta ema:name_rules (`_name_rules_hash`); `_catch_all_company`'s example corrected; the build round's catch-all test no longer assumes 'Medicaid of Kentucky' is the catch-all |

### Needs from others (fix round)
- F1's need of the build round still stands (F1 merged at 47b1ba0 without it): states.paid_proof's paid-before branch
  reads a payment under '<company> Medicaid Community Plan' as the state's Medicaid. The exact change and its test are in
  the build round's "Needs from others" below.
- states.paid_proof (F1's): for an outcome 'paid_after' whose payer's payment alone is under the threshold
  (`o['patient_cover']` decided it), the done text names the patient's part beside the payer's ("Paid later: $15.50 by
  ... and $40.00 by the patient on the line"); today it names the payer's $15.50 alone on a $55.50 item (the closure's
  own note and proof rows name both since this round).

### Left (fix round)
- Item B, the patient money's posting date: still not stored (ema_lines keeps sums only), so r1-3's patient proof row is
  undated. The change: a column `pat_paid_last` on ema_lines in ema_reports' DDL (ensure_schema adds it to older stores),
  filled in `ema_ledger._part_a` from pa_agg (own rows) and pa_pat/pa_pj (joined rows), read into `_LINE_COLS`,
  `_outcome_of` (`res['patient_paid_on']`), which `paid_in_ema` already returns as `patient_date`.
- G ("already worked" routed to call): unchanged from the build round (below).

## Items (status)
| # | Item | Function(s) changed | Status |
|---|---|---|---|
| A1 | bare $0 row: selfcheck blind-charge count and self-audit | selfcheck `_charges_since`, `_Facts` (answered = `ema_checks.line_answered_sql`, first answer = `ema_checks.first_answer_days`) | done |
| A2 | bare $0 row: turnaround | ema_checks `turnaround` (charge to the payer's first real answer, from the store's lines, which hold every ledger line; `first_answer_days` new: the never-answered rule's predicates `answer_row_sql` + the payer's reasons) | done |
| A3 | bare $0 row: the ledger's status class | ema_ledger `_part_a`: `rw_status_class(status, line_answered_sql)` (Resolved/Other on a bare row is gave_up) | done |
| A4 | bare $0 row: SECONDARY_NOT_BILLED | detect `_ema_lines_for` (only `run_secondary_not_billed` calls it): a second payer's EMA payment row answers only with `answer_row_sql`'s proof | done |
| B | bare $0 row then the patient's money: a date | not done (see "Left") | left |
| C | C5's patient, day and code check ignores the part | ema_checks `ema_denials`: `_name_day_codes_835(with_parts=True)` + `line_parts` + `part_fits` | done |
| D1 | self-audit windows: plan facts, a second payer's name read as its group | selfcheck `_windows(**facts)`, `_appeal_end` (new: the payer's window from the answer with its own limit; Kentucky Medicaid's corrected-claim window where it takes no appeal), `_second_end` (new: the second payer's group by EMA's name rules and `codes.second_claim_window`, as the product dates SECONDARY_NOT_BILLED); every `_cov_*` passes the facts. The audit stays independent of the items' own dates (an item closed or expired wrongly must still show as a gap: test_selfcheck's own test) | done |
| D2 | ema_checks' inside-the-window counts (C7, C9) | ema_checks `_still_recoverable` (the payer's name and reason as plan facts, the payer's own limit, `codes.rule_at`; a line an item covers by the item's own last day: `_finding_line_deadlines`, new) | done |
| E | C6 ignores the copay | ema_checks `_outcome_of` (`patient_paid`, `patient_cover`), `paid_in_ema`: a later payment plus what the patient paid on the line reaches the threshold (never for a short payment) | done |
| F | "Medicaid (other)" read as the state's Medicaid (C6, C5's same-day, the outcome's before/after split) | ema_checks `_catch_all_company`, `_same_payer_named` (new, fix6's reading: `states.MEDICAID_CATCH_ALL`), `_payer_matcher` (takes the EMA payer name), `_outcome_of`, `ema_denials` | done in ema_checks; states.paid_proof's own branch is F1's (Needs) |
| G | "Already worked, waiting" routed to call | not done (see "Left"): 56 items measured | left |
| H | rule 4.2 per service; policy row count = items listed | detect `_paid_share` (per service: patient name key else bill, date, code, part; paid when a payment stands on any answer or EMA shows the payer's money), `_share_patients`, `_share_ema_paid` (new); ema_checks `ema_denials`' EMA fallback per service; api_ema `_listed_policy_items` (new), `_payer_policies` | done |

Two older tests changed so their fixtures meet the bare $0 rule (their intent kept): tests/test_ema_wiring.py
`test_a_second_payer_whose_only_answer_is_in_ema_was_billed` (the second payer's EMA-only $0 answer now carries its
allowed amount); tests/test_selfcheck.py `test_blind_dollars_leave_out_claims_ema_shows_answered` (the line "with no
answer" now has none of an answer's figures, not only ins_answer_rows = 0).

## Needs from others
- F1 (states.paid_proof, the outcome's paid-before branch): `same = _same_payer(_grp_kind(pb.get("payer"))[0], denier)`
  reads a payment EMA posted under "<company> Medicaid Community Plan" as the state's Medicaid (the catch-all). Use
  `ema_checks._same_payer_named(_grp_kind(pb["payer"])[0], pb["payer"], denier)` there (the reading ema_checks now uses
  for C6). Test to add with it: an 835 Medicaid KY denial, EMA showing a payment posted under a company's Medicaid
  Community Plan name before the denial with the line settled: paid_before_other_payer (today: neither rule fires).

## Measured (private world copy of 2026-11-24, one server cycle each; tip 59f6822 against the branch b3c06a0; no fixer's
merge had reached the tip, so the measurement includes none; world copies deleted)
Items that moved: 8 (every one hand-traced, `$S/tk/units/fx-ema/hand_trace_verdicts.txt`: 7 right, 1 unclear, 0 wrong).
- appeared, SECONDARY_NOT_BILLED, first payer Medicare KY (CGS J15): 3 items, $48.34 (second payers: Medicare Supplement
  carriers 2, Humana Medicare Advantage 1): each second payer's only row on the line was a bare $0 row.
- to done by C6 (a later payment on a check plus the patient's share on the line reaching what the service brings):
  Anthem / BCBS Medicare Advantage 1 ($55.50), Humana Medicare Advantage 1 ($79.14), Anthem / BCBS 1 ($75.16), United
  Healthcare Shared Services EMA_DENIED 1 ($660.00, unclear: settled at UHC's allowed, an OA-274 $19.58 write-off on no
  item), UnitedHealthcare expired -> done 1 ($93.87). Work now loses 4 items ($869.80), expired loses 1 ($93.87).
- The first branch run (bffe448) also closed 2 items wrongly (a posting that moved a payment between lines without a
  check; a supplement's item valued at the balance the first payer left): fixed at b3c06a0 with tests, measured again.
A17 (rules A17), tip -> branch. Work-now view: already done 1,278 units ($424,964.77 -> $424,803.31), correct (closed)
10,445 -> 10,444, undecided (closed) 170 -> 171, undecided (open) 984 -> 983; closed wrongly 5 ($147.79) unchanged; already
worked 37, duplicate 66 unchanged. Status view: already done 2,821 -> 2,820, wrong amount (closed) 485 -> 489 (the C6
closures record the payer's own payment as recovered), undecided (closed) 3 -> 4.
Not measured on real data (no item moves): the blind dollars, the self-audit's gaps, the answer times, the policy table's
per-service shares and counts, the C7/C9 inside-the-window cents; their tests are the invented cases in tests/test_fx_ema.py.

## Left (not done in this round, with what is known)
- B (bare $0 row followed by the patient's money): on the tip's run 6 never-answered items carry the note (Tricare 2,
  VA Community Care 1, SIHO 1, Anthem 2; none of them has an EMA link in its evidence: they come from the 837 and exports
  passes). The ledger keeps each patient posting's date (postings.posted), but the store's EMA lines keep only the sums
  (pat_paid_joined), so the note cannot say whether the money came after the $0 row. The change: a column on ema_lines
  (the newest posting day of the patient's money on the line, own and joined) in ema_reports' DDL with its migration,
  filled by ema_ledger `_part_a` (pa_agg / pa_pat), read by ema_checks `_patient_cents` and said by `bare_zero_note`; then
  the rule (money after the row that settles the balance = the payer put it on the patient) is the owner's call, written
  in plans/owner_notes. Not started.
- G ("already worked" routed to call): on the tip's run 56 Work-now items ($29,975.16) have the route call because an A/R
  billing note already on the first A/R (its own date unknown) followed a long silence from the payer (`states._follow`,
  `work_stale`): Humana MA Medicaid DENIED_REFERRAL 14, Anthem CLAIM_DENIED_UNPAID 6, Medicare KY medical necessity 4, UMR
  6, Cigna 3, others. Note kinds: call or portal check 20, resubmitted 15, appeal 10, appeal and call 5, call and
  reprocessing 4, others 2. The hand decision from the raw A/R notes and 835s has not been made; next: trace each with
  `$S/tk/units/fx-ema/explore2.py`'s item list, then set the rule in `states._follow` / `_note_or_follow`.

## Suites
- tests/test_fx_ema.py 25 cases; the touched files' suites (test_ema_checks, test_ema_wiring, test_selfcheck,
  test_ema_ledger, test_answer_needs_proof, test_ema_reports): 478 passed after the two fixture updates (5 failed before
  them, all from the bare $0 rule and the audit's own dates, both resolved). Full suites: see the final report.

## Full suites (branch 793307f code, the C6 guards of b3c06a0 run on 3.11.1)
- python3 3.13: 3,369 passed, 4 failed, 29 skipped, 1 xfailed. The 4 were this branch's rule 4.2 change: 3 counted an
  accepted pattern by the items listed (0) where the run had set them aside: fixed (a pattern the run set aside on the
  owner's Accept keeps the run's count; a pursued one counts the Worklist's items). The 4th,
  tests/test_ema_wiring.py::test_remittance_denials_of_a_code_the_payer_never_pays_as_primary_are_its_policy, is OPEN:
  its 22 invented patients were named RAD0..RAD21, which the name key reads as one patient (digits are dropped), so the
  per-service measure saw one service; the fixture now names them RADA..RADV and the measure is right (22 answered, 0
  paid), but after the owner's Accept one G6001 item stays open (some rule after the fold makes an item for one of the 22
  services). First next step: find that item's signal, decide whether the fold must also take it, and make the test pass.
- 3.11.1 (SQLite 3.40.0): 155 failed, 47 errors; 198 of the 202 are `Connection.serialize` missing (allowed); the other 4
  are the same rule 4.2 tests as python3.

## Next command
Measure: `KEEP_WORLD=1 bash $S/tk/units/fx-ema/measure.sh $S/tk/units/fx-ema/brtree br1` (tip: `... tiptree tip0`, done),
then `python3 -I -B $S/tk/units/fx-ema/compare.py snap_tip0.sqlite snap_br1.sqlite moves1.json`.
