# Progress: unit gapfix (the never-answered gap), branch wip/gapfix

Brief: plans/briefs/2026-11-28_gapfix.md (with plans/briefs/2026-11-26_common.md, CLAUDE.md, plans/HANDOFF.md).
Base: origin/claude/ecstatic-fermi-orfcgo at b170aed. Worktree (this session): $S/tk/wt/gapfix. Private folder: $S/tk/units/gapfix.
S = /tmp/claude-0/-home-user-Billing-Optimizer/96e81489-2ad2-5c70-991d-e00db2e22749/scratchpad

## Order and state

| item | state | where |
|---|---|---|
| G8 one claim, one item | done, tests green, break and restore done; transition fixed after the real-data run | detect.py: `_item_links`, `save()` (same_link heir), `_note_waiting(cd=)`, `_waiting_ema_claims`, `_note_waiting_report`, `run_ema_report_claims` (waiting skip); tests/test_gap_one_claim.py |
| G3 archive holds unseen bills | done, tests green, break and restore done | archive.py `_held_bills` (HELD_UNSEEN, reads meta ema:unanswered_unseen 'claims'); tests/test_gap_unseen.py::test_the_archive_keeps_* |
| G2 unseen count by reason, dollars, Data tab | done, tests green, break and restore done | detect.py `_count_unseen`, `_finish_unseen`, `_ema_reports_from`, `_unseen_ema_lines`, `_save_ema_wiring`, no-ERA `*_cents`; ema_checks.claim_answers 'candidates'; insights `UNSEEN_WORDS`, `unseen_summary`, `_unseen_837`, `_feeds_837` (NO_ERA_LINES + `no_era_837_more`); api_ema `/api/ema/data` 'unseen', `/api/ema/unseen`, `/api/ema/unseen.csv`; static/data.js `dtUnseenHtml`, `dtUnseenRows` |
| G1 print-batch hand-off (fixed_b + never early) | done, tests green, break and restore done | detect.py `_ema_check_open_claims` and `_837_ema_answers` (hand-off saved), `run_ema_report_claims` (listed set, `_left_unsent`, waiting view rebuilt), `_left_unsent`, `self._export_day`; tests/test_gap_print_batch.py |
| G4 ERA start/stop window | done, tests green, break and restore done | detect.py `_run_837` (`no_era_at`, NO_ERA_WINDOW_DAYS = 90: 180 days around each claim's send, same min count and share); tests/test_gap_unseen.py::test_claims_sent_before_*, ::test_claims_sent_after_* |
| G5 A/R held-open line | done, tests green, break and restore done | detect.py `_837_ema_answers` (pending unseen, then `_ar_held_open`), `_ar_held_open`, `_emit_open_claim` (evidence 'ar_open' and hint); count 837_ar_held_open; tests/test_gap_unseen.py::test_a_claim_whose_line_emas_ar_holds_open_* |
| G7 waiting view names all or the remainder in dollars | done, tests green, break and restore done | detect.py `_finish_waiting` ('unnamed', 'ema_reports' from the notes); insights `_waiting_837` (remainder and notes in the detail); tests/test_gap_print_batch.py::test_the_waiting_view_counts_* |
| G6 second-payer bridge | done in the content bridge, with the guard; fixed after the real-data run (value, own status); tests green, break and restore done | ema_checks.claim_answers (`by_second`, `second_of`, `_second_group`, answers only by role 'secondary' postings and the second payer's reasons; 'second_payer', 'first_left_cents', 'open_cents' in the answer); detect `_answered_rule` (no patient or name answer for a second-payer claim), `_emit_open_claim` (valued at what the first payer left: the 837's AMT*EAF, else EMA's charge less insurance's payments and contractual adjustments; not listed only when that is $0: open_claim_second_nothing_left), `_837_ema_answers` (no EMA-claim status for it), the 837 and exports' passes (no `_export_links`, no heir key), `_waiting_ema_claims`, `_item_links`; tests/test_gap_unseen.py::test_a_claim_sent_to_the_visits_second_payer_*, ::test_the_first_payers_payment_*, ::test_a_second_payers_claim_*, ::test_a_claim_to_the_second_payer_never_stands_* |

## G8: what was built

- Within a run: a claim the exports' or the 837 pass puts on the waiting view now carries its claim (`_note_waiting(..., cd=)`);
  `run_ema_report_claims` finds the waiting claims' EMA claims once (`_waiting_ema_claims`, the passes' own
  `_ema_claims_found`), and a report check's proto whose claim id or EMA claim number belongs to a waiting claim is not
  emitted: counted `<check>_on_waiting_view` (meta ema:wiring) and noted on the claim's waiting row (a sixth element
  `{"check": "not_sent"|"rejected"|"no_answer", "ema_link", "status", "as_of"}`), and the waiting meta gets
  `ema_reports: {check: {n, billed_cents}}`. api.py `_waiting_row` reads the first five elements, so old readers are unaffected.
- Across runs: save() hands a worked item whole (status, notes, first_seen, history, faxes through finding_heirs) to the new
  item of the same EMA claim when one side is keyed by the EMA claim number ('link:<n>', no claim id) and the other by a
  claim id (or both by the same claim id). The heir records the old id in its evidence 'folded' list (rule same_ema_claim),
  which is the self-check's route for it (else the vanished check put the old row back beside its heir). Two different claim
  ids that meet on one EMA claim keep the old behaviour (a bill made again is a new matter, notes handed on).
- Tests (both store kinds): tests/test_gap_one_claim.py, 5 tests x 2. Break and restore: heir disabled -> the 3 identity
  tests fail (6); waiting skip disabled -> the 2 waiting tests fail (4); restored, md5 identical.

- After the real-data run (12 report-check items the self-check put back): a report check's item already on record from
  an earlier run, open and not stale, stays listed while its claim waits (`_listed_before`, count `<check>_kept_while_waiting`);
  only new ones are held. save() also lets the claim's own item take over an untouched open item of the same EMA claim
  (same_link for unworked items), so when the claim is listed there is one item. Test:
  tests/test_gap_one_claim.py::test_an_item_listed_before_its_claim_began_waiting_stays_listed_*; break and restore: each
  of the two rules disabled fails it (the second shows the duplicate: the claim's item and the old EMA-number item).

## G2/G3: what was built

- detect.UNSEEN_KEY (meta ema:unanswered_unseen) is now written from one place (`_finish_unseen`): every claim the passes
  count unseen goes through `_count_unseen` (the exports' `export_waits_unfound`, the 837 pass's `837_waits_unfound`, and,
  with no EMA report in the store, a no-ERA payer's due claims, reason `no_ema_reports`). Each claim is valued as
  `_emit_open_claim` values a listed one (a send to a second or later payer at what the payer before it left; not counted
  when it left nothing: ema_counts `unseen_second_send_nothing_left`), and given a reason in UNSEEN_REASONS order:
  no_ema_reports, ambiguous (claim_answers' new 'candidates'), before_ema_reports (service before `ema_reports_from`, the
  first service date any EMA source covers), second_payer_not_matched (837 SBR01 S/T, or EMA's line for the patient's day
  and code names this payer as secondary), voided_in_ema, ema_claim_not_found. The fixed 'cannot be told apart' text is gone.
- The meta keeps n, cents (valued), billed_cents, by_reason and by_group ({n, cents}), ema_reports_from, first_service,
  years_before, due_from/due_to, overdue_30, and `claims` [[claim id, 837 key, reason, group, cents, first service, sent,
  due]] most dollars first. The meta is not in diagnose.SAFE_META (the bundle shows its length and hash only).
- archive._held_bills holds the bill of every claim in that list (HELD_UNSEEN "counted unseen"), at any horizon.
- `_save_ema_wiring` now logs its sqlite error (runlog.caught) instead of `pass`.
- Pages: insights.unseen_summary (meta only, so the dashboard's copy shows it) feeds the data-health item `unseen_837` (title
  in dollars, reasons in words with dollars, top payers, the export that would cure the before_ema_reports part, and on a
  serverless host one sentence why the count is larger there) and the Data tab payload `unseen`. data.js draws the panel
  (by reason, by payer: ten and one remainder line) with a paged list and a CSV (`/api/ema/unseen`, `.csv`), most dollars
  first; the member id only with the names switch on and only where the store holds it (the Vercel copy keeps none). The
  meta's claim rows carry their codes (the Vercel copy has 837 rows only for the items' bills).
- No-ERA lines: every payer, the first NO_ERA_LINES (10) one by one and the rest on one line ('N more payers send no ERA:
  X claims, $Y billed'); each part of each line gives its billed dollars (listed, answered in EMA, unchecked).

## G1/G7: what was built

- A claim the exports' pass hands to ema_checks.not_sent (a newer A/R or Production status calls it never sent) is kept
  (`_left_to_not_sent`, as fixed_b); after not_sent's items are emitted, `_left_unsent` deals with each one not_sent did not
  list: an exports' claim whose first charge posting (else its bill date) is fewer than NOT_SENT_DAYS (INVALID_DAYS for
  Invalid) before the newest sign it is unsent (max of the report's as_of and the day EMA's exports reach) waits on the
  waiting view, due charge + days, with a note {"check": "print_batch", "since", "status", "as_of", "ema_link"}; else it
  is listed by `_emit_open_claim` as never sent. The 837 pass's own hand-off (`837_ema_not_sent`) is kept the same way and
  listed as the 837 pass lists a due claim, with the report's status as context. Counts: export_not_sent_waiting,
  export_not_sent_listed, 837_not_sent_listed.
- The waiting view is rebuilt at the end of run_ema_report_claims (the notes and the hand-off's rows in it); `_finish_waiting`
  counts the notes by check ('ema_reports') and the claims past WAITING_LIST_MAX ('unnamed': n, dollars, due days), and the
  Data tab's waiting line says both.

## G6: what was built, and what was left

- The content bridge (ema_checks.claim_answers) also accepts an EMA claim whose SECOND payer's group is the claim's payer,
  when no EMA claim of the claim's own payer has its content (chosen as any other: one, or the patient's one among several).
  Such a claim is answered only by that payer's own postings: payments EMA files under the line's secondary payer (role
  'secondary'), $0 answers with proof under it, reasons posted under its name. The one answered rule gives it no answer by
  the patient's other postings or by name (the first payer's payment or settlement never answers it).
- Its value (fixed after the real-data run): what the first payer left, by the claim's 837 (AMT*EAF, else the first payer's
  PR) or, when the 837 does not say, by EMA's lines (charge less insurance's payments and contractual adjustments). EMA's
  open balance is evidence only ('ema_open_cents', and a hint when it is below what the first payer left): the first cut
  valued it at the open balance and dropped it at $0, which took two claims to Medicaid as second payer off the list
  ($285.21 by their 837s) though the practice had only written the remainder off. Not listed only when the first payer left
  nothing (open_claim_second_nothing_left, meta ema:wiring).
- It is the second payer's matter, never its EMA claim's (fixed after the real-data run): it takes only its own claim id's
  status (EMA's claim's status is the first payer's), never marks its EMA claim as listed for the report checks, never holds
  it from the waiting view, and `_item_links` gives it no EMA claim for the heir rule. The first cut let a claim to Medicaid
  IN as second payer absorb a never-sent item for the first payer (UHC Medicare Advantage, $290.38: kept by the self-check).
- Left, by choice (not safe in this unit): the send-day twin and unanswered-before-send bridges still compare EMA's primary
  group only. Extending them needs `_links_answered` and `_unanswered_before_send`'s 'nothing answered' test restricted to
  the second payer's postings for such links; until then a second-payer claim only those bridges could find stays counted
  unseen with reason second_payer_not_matched.

## Next command

    cd $S/tk/wt/gapfix && python3 -m pytest -q -p no:cacheprovider tests/test_gap_one_claim.py

## Measurements so far

None yet (the final_world3 cycle is run once at the end, tip vs branch).

## Open questions / notes for the integrator

- The vanished self-check has no route for an item whose work moved to an heir with no bill to match; G8 records the fold
  in the heir's evidence instead of touching selfcheck.py (not this unit's file).
