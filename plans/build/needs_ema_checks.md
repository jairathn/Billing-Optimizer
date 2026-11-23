# needs_ema_checks.md: what existing files must change to wire in `remitwatch/ema_checks.py`

Builder 3: statuses, turnaround and lag, C1-C9, the line outcome (G3), W2-W5, the write-off policy table (amendment 8),
the reconcile summary. No existing file was edited. Import the module inside functions as `from . import ema_checks as ec`.
It imports `ema_reports`, `ema_ledger` and `codes`, and never imports `detect`: the Detector passes its own rules in as
callables. Every function runs the same way on a server store and on a serverless compact in-memory store (the test
`test_a_serverless_compact_store_gives_the_same_answers_as_a_server` compares the two answer by answer).

## 0. What the module gives the wiring

| Name | Use |
|---|---|
| `classify_checks(conn)` | C1: `ema_checks.cause` and `pair_trace`, `ema_months` measure `no835`. Run before C4 and C6 (C4 skips misnumbered pairs; C6 skips separate-payee checks) |
| `turnaround(conn, *, ledger=None, today=None)` | `{group: {'svc_p95', 'charge_p95', 'n'}}` in meta `ema:turnaround`. Pass the open ledger after a refresh so `charge_p95` is measured on every posting; without one it keeps the stored value while the ledger revision is unchanged, else measures the kept lines |
| `turnaround_days(conn, group, which)`, `turnaround_text(group, days)` | the number and the plain sentence for NO_ANSWER's "this payer usually answers in N days" |
| `posting_lag(conn)` | `{group: p95 days}`; p50 and p95 kept in meta `ema:posting_lag` (C5 deadlines, the C6 anchor) |
| `freshness(conn, today)` | report as_of dates, `production_fresh` (14 days), `ar_fresh` (8 days), newest 835, boxes |
| `claim_statuses(conn, claim_ids=, links=)` | C3a: newest status by as_of across the A/R, Production and the claim-status export |
| `claim_answers(conn, claims)` | C3e: `{claim_id: {'answered', 'how', 'on', 'paid_cents', 'link', 'bridge'}}`; `bridge` is claim_id, bill or content (E8) |
| `answers_through(conn, payer_group, newest_835, today=)` | C3d: the date through which Production shows this group's answers |
| `not_sent(conn, today=, rate=, skip_claim_ids=)` | C3b proto-findings NOT_SENT |
| `rejected_claims(conn, today=, rate=, skip_claim_ids=)` | C3f proto-findings REJECTED_PRE_PAYER from the reports' "Rejected" status (same id signal as NOT_SENT) |
| `unanswered_claims(conn, today=, skip_claim_ids=, rate=)` | G8 proto-findings NO_ANSWER |
| `ema_denials(conn, today=, rate=, ratio=, has_835_finding=)` | C5 proto-findings EMA_DENIED |
| `denial_as_contractual(conn, today=, ratio=)` | C7 proto-findings EMA_DENIAL_AS_CONTRACTUAL; the all-rows split in meta `ema:denial_as_contractual` |
| `unposted_remits(conn, today=)` | C4 proto-findings REMIT_NOT_POSTED; summary in meta `ema:unposted` |
| `line_outcomes(conn, findings, code_once=, today=)` | G3: one outcome per item (OUTCOMES), computed once per run |
| `paid_in_ema(conn, findings, outcomes, closes=)` | C6: close or hint |
| `written_off_in_ema(conn, findings, outcomes)` | C2: the write-off columns |
| `overlays(conn, findings, outcomes, today=)` | W2 and the `ema_*` display columns |
| `unworked(conn, findings, data_through=)`, `unworked_sql(data_through)` | W3: the number and the identical SQL filter |
| `ar_accounted(conn, findings=None, today=, save=True)`, `ar_list(conn, bucket, page, size=100)` | W4 buckets and one page of a bucket |
| `fax_vouch(conn, findings, outcomes, today=)` | W5: `{fid: {'vouched', 'code', 'message'}}` for `appeals_send` |
| `credits(conn, today=)`, `timely_writeoffs(conn, today=)`, `g2211_figures(conn)` | C8, C9 and the G2211 figures (reports) |
| `policy_summary(conn, findings, outcomes, today=)`, `set_policy(conn, pattern, decision, user)` | the owner's pattern table (O11), kept in meta `ema:writeoff_policy` |
| `reconcile_summary(conn, today=)` | everything `/api/ema/reconcile` shows (meta `ema:reconcile`) |
| `data_health(conn, today=)`, `alert_candidates(conn, today=)` | Data health items; REMITS_MISSING and EMA_STALE alerts |
| `claim_block(conn, claim_id=, bill=, links=, ledger=)` | the `ema` block of a finding and of the claim view |
| `checks_csv`, `unposted_csv`, `credits_csv` | the three CSV exports (no patient data) |
| `store_rules(conn)` | `rate`, `ratio`, `code_once`, `has_835_finding` built from the store's own tables, for the CLI and scripts |
| constants | `MONEY_CATEGORIES`, `CLAIM_SIGNALS`, `OUTCOMES`, `WRITE_OFF_OUTCOMES`, `NO_REMITTANCE_MESSAGE`, the meta keys |

**Proto-finding** (every list-returning check): `{'signal', 'id_signal', 'key_parts', 'category', 'severity', 'actionable',
'title', 'claim', 'expected_cents', 'shortfall_cents', 'carc', 'rarc', 'evidence', 'appeal_deadline', 'deadline_basis', 'hint',
'source': 'ema' (C4: '835'), 'ema_link', 'heir_keys'}`; EMA_DENIED also carries `ema_heir_keys` [(bill, date, code)]. The
wiring calls `_emit(p['signal'], p['category'], p['severity'], p['actionable'], p['title'], None, p['expected_cents'],
p['shortfall_cents'], p['carc'], p['rarc'], p['evidence'], key_parts=p['key_parts'], claim=p['claim'],
id_signal=p['id_signal'])`, then sets `appeal_deadline`, `deadline_basis`, `hint`, `source` and `ema_link` on the stored item.
Evidence never holds a name, a claim link number's URL or a note.

## 1. `detect.py`: the run, in the order of amendment 4.5

1. `build_links` (as today).
2. When meta `ema:map_dirty` is set: `ema_ledger.refresh(store, None, parts='B')`.
3. `ec.classify_checks(conn)`, `ec.turnaround(conn, today=...)`, `ec.posting_lag(conn)`.
4. `load`, baselines, line rules, claim rules (as today).
5. `run_ema_open_claims`: the status dictionary (`ec.status_class`, `ec.claim_statuses`); the export candidates checked with
   `ec.claim_answers` (an answered claim is not emitted; record `bridge` in its evidence) and `ec.answers_through` (a
   group whose Production answers stop before the item's due date waits); then `ec.not_sent(...)`,
   `ec.rejected_claims(...)` and `ec.unanswered_claims(...)`, each with `skip_claim_ids` = the claim ids the export path
   already emitted (that path wins). `rate` is the Detector's usual-rate lookup.
6. `ec.ema_denials(conn, today=, rate=, ratio=, has_835_finding=)` (C5). `has_835_finding(bill, date, code)` is True when
   the run already holds an 835 item for that line.
7. Aggregate rules, then `ec.denial_as_contractual(conn, today=, ratio=)` (C7; category contract, outside the money totals).
8. Second rates (as today).
9. `resolve_elsewhere` (archive branches first). Then, once: `oc = ec.line_outcomes(conn, items, code_once=self._code_once,
   today=today)` where `items` = the open items whose category is in `ec.MONEY_CATEGORIES` and whose signal is not in
   `ec.CLAIM_SIGNALS`, not stale, plus the run's EMA_DENIED items. Each item dict needs: finding_id, signal, category,
   line_key, claim_key, transaction_key, patient_control_number, service_date, payment_date, procedure_code,
   original_procedure_code, billed_code, expected_cents, shortfall_cents, carc, payer_group, paid_cents, evidence, status,
   stale, written_off_cents, written_off_on, appeal_deadline, modifiers.
   Then `pe = ec.paid_in_ema(conn, items, oc, closes=EMA_CLOSES)`:
   - `action == 'close'`: `_mark_paid(f, note=pe[fid]['note'])` and add `pe[fid]['used']` (check, posted, link) to
     `used_payments`, so a newer pull that no longer shows the payment reopens the item (as today). A person's status is
     never replaced.
   - `hint_other_payer`, `hint_partial`, `hint_paid_before`: set the item's `hint` to `pe[fid]['hint']`; never close.
10. `ec.unposted_remits(conn, today=)` (C4; category fees, actionable review, work type post).
11. `wo = ec.written_off_in_ema(conn, items, oc)` (C2) and `ov = ec.overlays(conn, items, oc, today=)` (W2). Copy every
    returned field onto the item. `written_off_cents` is already the larger of the ERA-lines figure and EMA's net, never a
    sum. Store `evidence_level` too (in the evidence JSON as `ema_writeoff_evidence`, or a column): the pages must say
    "Recorded by EMA's ERA export" or "Inferred from the posting date".
12. `save`, with:
    - `heir_keys` [(id signal, key)]: the new item inherits the work (status, notes) of the older items named (a worked
      NOT_SENT or NO_ANSWER item for the same claim hands its work to the EMA_DENIED item, as the claim's answer);
    - `ema_heirs` from EMA_DENIED's `ema_heir_keys` [(bill, date, code)]: the first 835 item on that line inherits a worked
      EMA_DENIED item (Started if it was started or sent, else To do; its notes; an event `signal_changed`), and the EMA item
      then goes stale;
    - meta `ema:keep_dirty` set when the set of bills with live items changed, so the ledger's next Part A keeps their
      lines (`ema_ledger.refresh` reads and clears it).
13. `ec.reconcile_summary(conn, today=)`, `ec.ar_accounted(conn, items_of_the_run, today=)`, `ec.unworked(conn, items,
    data_through=...)` with `data_through` = Production's as_of when `freshness()['production_fresh']`, else the newest 835.

**New finding columns** (`detect.EXTRA_FINDING_COLS` and `api.FINDING_COLS`): `written_off_source`, `written_off_kind`,
`written_off_when`, `ema_link`, `ema_status`, `ema_status_src`, `ema_as_of`, `ema_balance_cents`, `ema_worked_on`,
`ema_worked_how`, `source`, `ema_outcome`, `ema_outcome_on`.

**Rules version:** `DETECT_RULES` 2.7 (Step 2) to 2.11 (Step 8); `_rules_hash` adds `ema_reports.py`, `ema_ledger.py` and
`ema_checks.py`.

**Outcome values** the pages and filters must know (`ec.OUTCOMES`): paid_after, paid_after_other, paid_after_partial,
paid_before, patient, lost_to_timely_filing, written_off_while_pending, auto_written_off, written_off_at_posting,
contractual_at_posting, written_off_later, contractual_later, written_off_by_policy, action, closed_unexplained, open,
shared_line, not_found, not_covered. `contractual_at_posting` / `contractual_later` also cover an underpayment or downcode
closed by EMA's routine reduction (CO-45 and the like): `contractual_routine` holds that amount; it is not a C2 mark.

## 2. Signal lists, actions, labels
- `EMA_CLAIM_SIGNALS` gains NOT_SENT (and REJECTED_PRE_PAYER when it comes from the reports; both use id signal
  `EMA_OPEN_CLAIM` with the claim id, or `link:<n>` when the A/R does not know the claim id).
- `actions.NEVER_HIDDEN` gains NOT_SENT and REMIT_NOT_POSTED.
- Keys: NOT_SENT, REJECTED_PRE_PAYER, NO_ANSWER `(claim id or 'link:<n>',)`; EMA_DENIED `(link, reason class)`;
  REMIT_NOT_POSTED `(transaction_key,)`; EMA_DENIAL_AS_CONTRACTUAL `(payer_group, reason, year)`.
- Labels: NOT_SENT "Claim never left EMA" / "Not sent from EMA"; REMIT_NOT_POSTED "Paid by 835, not posted in EMA" /
  "Payment not posted"; EMA_DENIED "Denied in EMA, no remittance on file" / "EMA denial, no 835";
  EMA_DENIAL_AS_CONTRACTUAL "EMA writes off this payer's denials as contractual" / "Denials posted as contractual".

## 3. `insights.py`
- `WORK_TYPES` gets `post` ("Post the payment") before `check`; `_WORK_OF`: post = {REMIT_NOT_POSTED}; fix gains NOT_SENT.
- `work_type` and `kind_sql` treat EMA_DENIED as CLAIM_DENIED_UNPAID; EMA_DENIAL_AS_CONTRACTUAL is work type check.
- `data_health` appends `ec.data_health(conn, today=)` (same shape: key, ok, title, detail, who): ema_reports_missing,
  ema_ar_stale, ema_production_stale, ema_report_held, ema_columns, ema_map, ema_link_conflicts, remits_missing, credits,
  ema_status_words.

## 4. `api.py`
- `GET /api/ema/reconcile`: `ec.reconcile_summary(conn)` (or meta `ema:reconcile` when the run wrote it today).
- CSV, all no-store: `/api/ema/checks.csv?cause=&from=&to=` -> `ec.checks_csv(conn, cause=, date_from=, date_to=)`;
  `/api/ema/unposted.csv` -> `ec.unposted_csv(conn)`; `/api/ema/credits.csv` -> `ec.credits_csv(conn)`.
- `GET /api/ema/ar?bucket=&page=` -> `ec.ar_list(conn, bucket, page)`; buckets: waiting, chase, paid_elsewhere,
  denied_todo, denied_started, denied_sent, denied_worked, denied_no_item, not_sent, settled, facility, unbilled, other,
  credits. It never writes meta.
- `ema=` filter through `api._filters` (so counts, tiles and lists agree): written_off = `written_off_cents > 0`;
  contractual = `written_off_kind = 'contractual'`; paid_other = `ema_outcome = 'paid_after_other'`; on_ar = `ema_link IN
  (SELECT link FROM ema_ar)`; ema_only = `source = 'ema'`; worked = `ema_worked_on IS NOT NULL`; unworked = the SQL of
  `ec.unworked_sql(data_through)` (the test proves it equals `unworked()`'s count); add auto_written_off =
  `ema_outcome = 'auto_written_off'` for the "Auto write-offs still appealable" filter (with `appeal_deadline >= today`).
- `GET /api/findings/{id}` and `/api/claims/view`: `ema` = `ec.claim_block(conn, claim_id=, bill=, links=, ledger=lg)`;
  on a server pass the ledger opened read-only at `ema_ledger.ledger_path(db_path)`; on Vercel pass None (the block then
  says the postings are kept on the server).
- `/api/brief` `ema`: as_of dates from `ec.freshness`, `unworked` from meta `ema:unworked`, the A/R buckets from meta
  `ema:ar_accounted`, counts by signal and outcome from the findings table (each number equals the count of the list it opens).
- Policy (owner decision O11): `GET /api/ema/policy` -> `ec.policy_summary(conn, items, outcomes)` (needs the run's
  outcomes: keep `oc` in meta or recompute with `ec.line_outcomes` and `ec.store_rules(conn).code_once`);
  `POST /api/ema/policy {pattern, decision: pursue|accept}` -> `ec.set_policy(conn, pattern, decision, user)`; any signed-in
  user, recorded with who and when; the next run turns matching write-offs into `written_off_by_policy` (they leave the work
  queue and stay findable).
- `POST /api/appeals/send` applies W5: when the 835s alone would hold the item as stale, call
  `ec.fax_vouch(conn, [item], outcomes_for_it, today=)`; send when `vouched`, else hold with its `code` and `message`
  (production_old, not_mapped, paid_after, no_balance). EMA_DENIED items are always held with code `no_remittance` and
  `ec.NO_REMITTANCE_MESSAGE`. A line EMA closed with a write-off still counts as open here (amendment 8: a write-off is
  not a payment), so auto write-offs can still be faxed.
- `/api/ema/reconcile` and the Data tab: `ec.c1_summary(conn, from, to)` adds `no_check_cents` (insurance payments EMA
  posted with no check number, which no remittance can match) and `posted_cents`; both are taken over the whole months the
  range touches, so pass month ranges.

## 5. Alerts and the weekly email
- `alerts.AlertEngine` raises `ec.alert_candidates(conn, today=)`: REMITS_MISSING per payer group and posted month (feed gap
  of $2,000 on at least 2 checks; key ('REMITS_MISSING', group, month)); EMA_STALE when the A/R or Production is more than 8
  days old (clears itself).
- Weekly email: the unworked number, C6 closures of the week, NOT_SENT and REMIT_NOT_POSTED counts, auto write-offs still
  appealable, the A/R buckets.

## 6. `pipeline.py`, `cloud.py`, `cli.py`
- After `ema_ledger.refresh(store, lg)` with the ledger open (server cycle and the cloud apply run):
  `ec.turnaround(conn, ledger=lg)`, so `charge_p95` comes from every posting, not only the kept lines.
- `cli`: an `ema-checks` command (summary, CSVs, policy table) builds its rules with `ec.store_rules(conn)`.

## 7. Pages
- The item panel shows the line's outcome (`ema_outcome`) before EMA's claim status, which is context only ("EMA calls
  the claim Resolved because other lines were paid").
- Chip "Auto write-off" and the wording of amendment 8 for auto_written_off, written_off_while_pending and
  lost_to_timely_filing; the evidence line from `evidence_level`.
- Denials filter "Auto write-offs still appealable", sorted by dollars then deadline; a count on Today.
- The policy table page: pattern, items, dollars, still appealable, auto, later-paid share, decision.

## 8. Not built in this module
- Amendment 9 (added 2026-11-22, during this build): `item_story`, `item_verdict` and `next_step`. The facts they need are
  here: `line_outcomes` (timeline: anchor, payments before and after with check tails, reasons with dates, write-off
  timing and evidence, balance, status), `claim_block` (A/R rows, lines, postings), `paid_in_ema` notes, `fax_vouch`.
- C10 (patient-side write-offs on insured lines) and the C11 age table: deferred (X5).

## 9. Measured on the real files (aggregates; store copy after a full cycle of current code, scratch ledger)
C1 Jan 2025-Sep 2026: $2,577,562 on unmatched checks plus $111,800 posted with no check number = $2,689,362 (spec
$2,742,788; the store holds 66 more Sep 2026 835s, $53,354, than the archive the spec measured, which closes the gap to
$72). C4 within 365 days: 109 not posted ($31,504) and 62 posted short ($11,476). C6: 148 closures ($17,733), none on a
facility claim or a separate-payee or misnumbered check; 361 paid-before hints, none closed. C2: 982 items ($85,268)
inside the deadline. C5: 635 lines ($112,186 expected) inside the window, 0 items whose patient-day key has an 835 line.
C7: $458,517. C8: insurance -$22,979, patient -$34,621. W4: 8,419 rows, $1,284,521.32, buckets add up exactly. W5: of 2,359
items inside the window with 2025+ service, 2,069 vouched, 159 paid after, 127 closed with no balance, 4 not mapped. All
checks run in about 18 s on this machine.

## 10. Fix review (EMA fixer): what changed in `ema_checks.py` and what the wiring must do

1. **Detection stores the dashboard's figures.** On the whole store, after the checks and before `cloud.build_serving`,
   detection must call `ec.unposted_remits(conn, today=, save=True)`, `ec.ar_accounted(conn, today=, save=True)` (it
   writes `ema_ar.bucket` and meta `ema:ar_accounted`) and last `ec.reconcile_summary(conn, today=, save=True)` (meta
   `ema:reconcile` and `ema:c1_cells`). The copy holds only part of the store, so these figures are read from what was
   stored. Measured: every page reads the same on the copy as on the store, and reading them does not change the copy.
2. **Pages never write.** API GETs use `reconcile_summary(conn, save=False)`, `ar_list(conn, bucket, page)` (its `pending`
   counts A/R rows read since detection that the copy cannot bucket yet), `unposted_csv`, `checks_csv`, `credits`,
   `credits_csv`, `data_health`, `freshness`, and `c1_summary(conn, date_from, date_to)` (over the whole months the range
   touches; on the copy it adds up the stored month cells). On the copy each gives what detection stored and writes nothing; none makes a salt.
3. **C2 dollars.** When a write-off has no priced shortfall, the item is capped at charge less paid and marked
   `written_off_basis = 'charge_less_paid'`. Dollar totals (KPIs, insights, the email) should leave these cents out or show
   them apart. Real data: 88 such items, 24 inside the deadline ($2,137).
4. **Payer equivalence.** `classify_checks` learns which EMA payer names stand for an 835 payer (at least 3 checks and at
   least 5% of the name's checks), stored in meta `ema:payer_equivalence` (97 names on the real data). Line outcomes use it
   for "the same payer". The own-payment trace needs `payer_name` and `transaction_key` on the finding rows it is given.
5. **What moved on the real data** (store copy after a full refresh with the fixed code; files as of 2026-11-20):
   C1 total unchanged ($2,577,561); 58 partial misnumbered pairs ($3,174) moved checks into `misnumbered` (191 checks,
   $11,527). C4 inside 365 days 171 -> 164 traces ($42,980 -> $42,240; posted short 62 -> 55). C6 closures 148 -> 161
   ($17,733 -> $18,786): 102 denial, 57 bundling, 2 underpayment ($28); paid-before hints 361 -> 328, other-payer hints
   56 -> 43, partial 44. Line outcomes `paid_before` 375 -> 335; 100 items carry a takeback ($129,737). C2, C5, C7, C8, W4,
   C3b, C3e, C3f and G8 unchanged. The 2 underpayment closures: a later payment from the same payer (by the learned
   equivalence, not by name) of the shortfall less 2% (sequestration), 267-274 days after the anchor. They look right.
6. **C4 candidate for the real-data fixes.** Of 124 "not posted" traces paid since 2025-11-20 ($34,493), 39 ($5,262) have
   EMA insurance payments on every one of their claims (by the claim-link map) within 7 days before to 120 days after the
   835 date, under another check number; 26 of them ($3,517) for the same amount within $1; for 7 traces the trace number
   itself is among those EMA checks. Suggest: before itemizing a trace as not posted, look at the claim payments on its
   mapped links, and when every claim was posted for the same amount under another number, do not itemize it (or say
   "posted under another check number"). The other 83 traces ($28,812) have no EMA insurance payment on their claims near
   the date. This is above the amended $11.5-25K; the self-funded TPA block is 66 traces ($12,888) inside 365 days.
7. **Damaged check numbers** (scientific notation in EMA's spreadsheet): C6 counts a payment under one as 835 money unless
   it is paired, and C4 never itemizes a trace such a check could hold (`damaged_n`, `damaged_cents` in its summary).
8. **Status ties.** Claim statuses, overlays, not-sent and rejected lists order by day, then by source rank, so a same-day
   A/R status wins over the Claim Status export. Nothing to wire.
9. **Tests.** `test_unworked_equals_its_filter` adds `ema_worked_on` and `written_off_when` to findings only when missing,
   since detect now adds them.
