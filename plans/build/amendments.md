# Amendments to the EMA reports spec (tech lead, before the build)

These decisions override `arcoll/design_final_spec.md` wherever the two conflict. Section numbers (6.4, C3, W4) refer to the spec.
They rest on the eight lens analyses (`eg2/lens_results.json`), the sizing scripts (`arcoll/sizing/`) and nine small checks of
my own (`build/techlead/scripts/t1`-`t9`, aggregates only). Every figure below is an aggregate. None of them, and no payer alias, host name or
identifier from the files, may go into code, tests or docs.

Read section 0 first. Sections 1-3 say what changes and why. Section 4 is the contract the three builders and the wiring phase
build against. Section 5 is the amended step table with the acceptance figures. Section 6 lists owner decisions.

## 0. The decisions on one screen

1. **The A/R export is not the whole A/R.** It holds the 0-30 day bucket, the denial bucket (any age) and undated rows. Absence
   from it never means closed or paid. Exposure is "Current A/R, else Denial Amount" (never their sum). (E1)
2. **The A/R cannot be Build 2's clock.** The Step 2 "Build 2 from the A/R" rule is cut: 199 of the 212 rows ($50.5K of $53.1K)
   it would list were charged within the payer's p95, not unanswered. Unanswered claims come from Production instead (Step 5). (E2, G8)
3. **Closed is not paid.** A claim status is claim-level and undated. It never closes an item, never answers a claim on its own,
   and is never shown as the item's outcome. The outcome shown is the line's own, from postings. (E6, G3)
4. **The 835 decides wherever it exists.** C6 closes only on EMA payments under checks with no 835 (and not misnumbered, not the
   ASC's). Later payments on 835 traces are the archive rules' job. (E5)
5. **Twin claims (the ASC's facility claims) never stand in for the professional claim.** Map, answers and closures exclude
   facility links; patient-day mapping must agree on charges. (E7, E8, G2)
6. **Credits include negative Denial Amounts.** Medicare primary credits over 60 days are about -$16.8K on 20 rows, not $0. (E3)
7. **The misread-rows migration rule would purge a genuine Claim & Claim Status export** (one 23-row source in the current store).
   New rule: no claim id, bill id, bill item id or patient id on any row, and the original file re-recognised as a report. (E4)
8. **VA naming is learned from payer ids**, not left as an owner alias: the weekly exports give payer id VACCN for the EMA name
   EMA now uses for VA Community Care. Without that, C1's VA figure ($849K) cannot be reached. (E9, G7)
9. **Vercel reads Production only from files of 60 MB or less** (zip the big CSV, about 12-15 MB). Direct streaming of 160-206 MB
   CSVs from Dropbox is cut. Attempts are counted in the state folder before a read starts. (E12, X2)
10. **Module boundaries move slightly**: the status dictionary, bill parsing and EMA payer grouping live in `ema_reports` (the
    readers need them first; `ema_checks` re-exports `status_class`); C1 cause assignment moves from `ema_ledger` Part B to
    `ema_checks.classify_checks`. (section 4)

## 1. Errors in the spec and the corrected rules

**E1. The A/R report is a window, not the A/R (affects 1, 4, 6.4, 6.5, C3, C8, C11, W4).**
- Measured: every row with a non-zero Current A/R and an aging date has its aging date within 30 days of the pull (8,609 of
  8,609). The rows aged older (597 more than 30 days before the pull) appear only because they carry a Denial Amount, and their
  Current A/R is $0. 433 rows have no aging date (unbilled rows, paper claims waiting to print, credits). 77 rows carry both
  amounts, always equal. Production shows 2,696 lines ($236K) with a positive balance that the export leaves out, including
  about 660 never-answered lines.
- The aging date is not "reset every cycle": it equals the line's last posting date on 91% of rows. It is still never a payer
  turnaround clock.
- Corrected rules:
  - `ema_ar.exposure_cents` = Current A/R when it is not zero, else Denial Amount. Never add the two.
  - Each A/R file's account records `aging_window`: 30 when every non-zero Current A/R has an aging date within 31 days of the
    file's as_of, else NULL (a full export). The catalog and the Data tab show it.
  - The reader accepts extra bucket columns when EMA offers them (any normalised name ending in `a/r ($) (aging)`, or `total a/r`)
    and then sets exposure from their sum. When only the current bucket is present, nothing may treat absence from the A/R as
    closed, paid or settled.
  - `ema_ar_items.gone_since` is renamed `absent_since` and means only "not listed in the newest snapshot". No rule reads it as an outcome.
  - The A/R shrink hold (6.4) compares only rows an honest re-export must still list: rows with a denial amount, and rows whose
    aging date is within (30 minus the gap in days) of the new as_of. It applies when the current snapshot is at most 10 days
    older (not 21). The 40% and 5% thresholds stay.

**E2. "Build 2 from the A/R" lists late charge entries, not unanswered claims (C3c, C3d, Step 2 acceptance).**
- Measured on the real files: the rule as sized (primary Payment Pending, older than max(p95, 30) days from service) gives 212
  rows ($53.1K). For 199 of them (79 claims, $50.5K) the first charge was posted within p95; 11 more have an insurance answer
  already posted (a resubmission). One row ($34) is plausibly unanswered.
- With the spec's own sent-date chain (submission date, else first_seen, else first charge, else service date) the rule lists
  nothing on a first snapshot, so its expected figure (about 90 claims, $54.1K) could not come from it.
- By construction an export that lists only lines posted in the last 30 days cannot show a claim waiting longer than any payer's
  p95 (35-100 days).
- Corrected rule: C3c and the A/R clock in C3d are cut (X1). Unanswered claims older than the export window come from Production
  (G8, Step 5). The A/R still supplies statuses (NOT_SENT, REJECTED), the denial bucket, credits and the panel.

**E3. C8 leaves out the negative Denial Amounts (Step 2 acceptance).**
- Measured: insurance credits are -$22,979, not -$2.5K: Current A/R -$2,515 on 76 rows (secondary and tertiary) plus negative
  Denial Amounts -$20,464 on 50 primary rows.
- Medicare primary credits whose aging date (last posting) is more than 60 days old: 20 rows, -$16,752. One visit is about
  -$11.3K and looks like an unposted reversal (the 835s show payment, reversal, payment; EMA holds both payments).
- Medicare Advantage primary credits over 60 days: about -$3.3K on 24 rows. Patient credits: -$34,621 on 80 rows.
- Corrected rule: a credit is a negative exposure (E1). Its age counts from the aging date (the line's last posting) when there
  is one, else from the service date. Data health `credits` turns not ok on any Medicare primary credit over 60 days (it will,
  on the real files), or on other credits over 60 days totalling $500 or more. Medicare Advantage credits are listed separately.
- From Step 3, C8 also lists the insurance-side credit balances Production shows and the export leaves out (old credits have no
  recent posting, so the window drops them). Measured (reverse lens): 109 Medicare primary visits over 60 days, about -$7.8K,
  are not in the export.

**E4. The misread-rows migration would delete a genuine export (6.10).**
- Measured on the store copy after a full cycle: one Claim & Claim Status source (23 rows) has no claim id on any row and does
  have coverage type, so the spec's rule would purge it. It carries bill ids, location, business unit, charges and rejected
  amounts, none of which an A/R or Production file has. That store copy holds no misread A/R rows at all, so the live stores
  may or may not.
- Corrected rule:
  - A candidate is a source read as `claim_status` in which no row has `claim_id`, `bill_id`, `bill_item_id` or `patient_id`.
  - It is confirmed only when its original file is recognised as a report by `ema_reports.recognize`: on the server from
    `files.source_path` (or the upload's kept copy); serverless, when the queued Dropbox entry is fetched.
  - Confirmed: delete its `ema_rows`, `ema_sources` and `files` rows, and queue it as `waiting` with its origin.
  - Not confirmable (original gone): keep its rows; list it on the Data tab as "may be a misread report".
  - Serverless: queue first, purge on confirmation. Rows are never deleted before the file is seen to be a report.
  - Rows of family `unknown` are deleted as the spec says; their `files` row becomes kind `ema:unknown`, status `not_read`.

**E5. C6 counts later payments on 835 traces, which contradicts 7.3 ("the 835 decides").**
- The spec's C6 counts payments "under a check that is neither the finding's own trace nor an 835 trace dated on or before it",
  so it counts 835 payments dated after the finding. RemitWatch's own rules saw those payments and kept the item open for a
  reason (units, a second payer, an addition to a short payment). EMA's line merges units and cannot do better.
- All five downcoding and seven underpayment closures in the spec's figure came from such 835 payments.
- Corrected rule: a payment counts for C6 only when its check has `in_835 = 0`, its C1 cause is not `misnumbered` or
  `separate_payee`, it is on the finding's mapped, non-facility link, and the claim carries the code once on that date.
  Same payer group and at least 50% of expected closes (95% of the shortfall for downcoding and underpayment, as specified).
  The archive cases go to the detect unit (section 7).
- Estimate from the prod-link lens: 227 open denial and bundling findings (162 and 65, about $28.5K) have a later payment of at
  least 50% of expected on checks with no 835, from any payer. Expect roughly 180-230 closures after the same-payer test, and
  about 0 downcoding or underpayment closures. (Another 108 such items are NO_ANSWER or REJECTED claims: C3e's, not C6's.)

**E6. Claim status is claim-level and undated (C3a, 5.5 overlays, W4, the biller's complaint).**
- Measured: 90.5% of found items sit on a claim EMA calls Resolved; 1,375 zero-paid items sit on a Resolved/Paid claim with no
  insurance payment on their own line; 13 never-answered claims read Resolved/Paid with no posting at all.
- Corrected rule:
  - A status may route an item (not sent, rejected, denied, pending). It never closes an item, never marks a line paid, and on
    its own never removes a claim from NO_ANSWER once Production covers it. An answer needs an insurance posting on the link.
  - Each finding gets `ema_outcome` and `ema_outcome_on`, the line-level result from postings (see `line_outcomes`, 4.3).
    Badges and the drawer show the outcome first ("EMA: written off at posting, CO-197, on <date>"; "EMA: paid $X on <date>,
    no 835") and the claim status second ("claim status: Resolved/Paid").
  - W4's "settled since the pull" and "paid elsewhere" buckets need postings, not a status.

**E7. The link map: $0 answers must vote, the payer claim number must not, and patient-day must check charges (7.2).**
- Measured (fields-identity lens): a vote from payment rows alone reaches 75.8% of open items at bill level; adding EMA's $0-answer
  rows (a check number and no amount, 60,020 rows) reaches 91.0%; breaking ties with the name key reaches 95.8%. The payer claim
  control number is filled on 1.1% of links and agrees with the tied bill on 88% of them (12% would map wrongly).
- Corrected rule, in order of precedence:
  1. `ar`: the (claim id, link) pairs from the A/R.
  2. `payment`: every linked insurance row whose check is an 835 trace votes, payment rows and $0 rows alike. Candidates are the
     trace's lines with the same date and code (or original code), narrowed in turn by: same name key and same amount; same name
     key; same amount. A link takes a bill holding at least 2/3 of its votes; otherwise it is a conflict.
  3. `patient_day`: as specified, plus: the candidate link's charge for every matched (date, code) must equal the 835 line's
     billed charge within 2%, and a link marked `facility` is never a candidate.
- `pccn` is dropped as a method. The column stays as a marker (a corrected claim is likely when it is filled).

**E8. The C3e patient bridge walks into the twin-claim trap.**
- Measured (missing-835 lens): a patient + date + code join reads 111 open items ($16.0K) as paid because the ASC's facility claim
  for the same service was paid after the finding; for 54 of them ($8.7K) the professional claim was never paid.
- Corrected rule: the bridge for a claim with no remittance is the content bridge: the claim's (service date, code, charge) rows
  from the weekly exports equal the charge rows of exactly one non-facility link, with the payer group agreeing. Measured right
  99.8% of the time where the true link is known. The EMA-patient-id-to-name-key bridge is cut (X3).

**E9. O3's default contradicts C1's acceptance figure, and the evidence already settles O3.**
- C1 expects VA $849K. With "not aliased" (the O3 default) the VA cause would hold only the payments under the two VA-named EMA
  payers (about $354K); the rest would fall to `no_era`.
- Evidence (missing-835 lens): the weekly exports carry payer id VACCN on that EMA payer name (346 rows), 174 patients were paid
  under both its old and new names, and paid equals allowed on 2,488 of 2,515 rows.
- Corrected rule: `ema_reports.learn_payer_aliases` maps an EMA payer name to VA Community Care when at least 20 of its export
  rows carry a payer id and at least 90% of those are VA network payer ids (`VACCN`, `TWVACCN`). The learned map is kept in meta
  `ema:payer_aliases_learned` (it must survive the weekly exports being dropped). `REMITWATCH_EMA_PAYER_ALIASES` still overrides.
  `ema_reports.payer_group_of` applies the VA pattern (the codes.py VA rule plus `VACAA`, tested before every other rule) before
  `codes.normalize_payer`, so EMA results are right before codes.py is fixed; the codes.py fix (VA above OPTUM, plus VACAA)
  still ships in Step 2.

**E10. Bills of facility claim ids are cut wrongly.**
- Measured: 120 A/R claim ids (143 rows, $96,233 of exposure) start with F and are 14 characters (F plus the usual shape).
  `detect.bill_of` does not parse them and returns the whole id.
- Corrected rule: `ema_reports.bill_of_claim` strips a trailing `C` + 1-3 digits from both shapes and keeps the F;
  `is_facility_claim` is true for the F shape. Facility claim ids mark their links `facility = 1`.

**E11. "Unit Allowable" is not a contract rate.** Measured: it equals the charge on 95% of one-unit Blue Cross lines and sits 3-10%
above the charge on 99.5% of Medicare KY lines. It is not read, not stored in the ledger and not in `ema_lines`.

**E12. The Vercel streaming plan does not fit at the speeds it allows, and a killed run never counts its attempt (8.2, 8.3).**
- At 2 MB/s the 206 MB CSV takes about 100 s to download; with the store load, staging, apply and Part A/B the read finishes
  after the big-file deadline (budget minus 75 s), so the spec's "room for 2 MB/s" is not there.
- `download_chunks` waits up to 300 s for a read. A stalled stream outlives the 240 s function, the platform kills the run, and
  the attempt counter (kept in the store, which is never saved) never moves: the file is retried forever.
- Corrected plan: X2 (files of 60 MB or less only). The attempt is written to `ema_attempts.json` in the state folder before
  any Production read starts; three attempts hold the file. Every Dropbox read used for reports has a read timeout of 30 s.

**E13. C4's posting differences must compare claim payments, not deposits.** Measured: EMA's check total equals the 835's claim
payments on 96.7% of shared checks, but equals the net deposit on only 185 of 9,927 checks that carry a PLB. Compare with the sum
of the 835's claim paid amounts (CLP04), never BPR02.

**E14. WITHOUT ROWID primary keys cannot hold NULL.** A missing check number, reason group, code, payer or detail is stored as ''
in `ema_line_payments`, `ema_line_reasons`, `ema_months`, `ema_lines` and the new `ema_link_events`.

**E15. The test host conflicts with the repository rule.** The spec's fixture host is an invented label under the vendor's real
domain, which is the form of an EMA tenant host. Fixtures use `https://example.ema.test/ema/ws/v9/qlikRedirect/claim/{n}`. The
prefix validator takes its domain from the module constant `ema_reports.CLAIM_HOST_SUFFIX` (default `ema.md`); tests set it to
`ema.test`. The repository scan test fails on any literal URL whose host is a label under `ema.md`, anywhere in the tree (the
validator builds its pattern from the constant, so it holds no such literal).

**E16. C2 "moved to the patient" and patient money.**
- Measured: for 251 items the archive already holds a later remittance that moved the amount to the patient (PR).
- Corrected rule: `moved_to_patient` only when no 835 for the bill, date and code assigns PR after the CO denial. Patient payments
  never resolve, close or mark an underpayment or downcoding item (on 344 underpayment items they are ordinary coinsurance).

**E17. C5 can duplicate an 835 finding when the map failed, and over-sizes second-payer denials.**
- Corrected rules: an unmapped link counts as having no 835 only when none of its checks is an 835 trace and no 835 line exists
  for its (name key, date, code). When another payer paid the line before the denial, expected = the balance left at the
  denial (charge minus earlier payments and contractual adjustments), not the charge.

**E18. Privacy holes.**
- A Python conversion error carries the cell text (`could not convert string to float: '...'`). The readers catch every
  conversion internally and count it; no exception leaving a reader, and no log line or `status_reason`, may hold a cell value.
  The router logs only the exception type for report files.
- The dashboard copy excludes `person`, `name_key`, `mrn_key` and `note_hash` from every EMA table, not only from `ema_ar`.

**E19. Acceptance figures that cannot be right as written:** Step 2 Build 2 from the A/R (E2); Step 2 credits "Medicare primary
about $0" (E3); Step 5 C6 downcoding and underpayment closures (E5); Step 6 VA $849K under the O3 default (E9); Step 6 "each
cause within 5%" for heuristic causes; Step 6 C4 $43.8K (the lenses measure $11.5-20.6K of 835 payments EMA never posted; the
self-funded block of about $24K is probably misnumbered and paired). Section 5 has the amended figures.

## 2. Cuts and deferrals

**X1. Cut: A/R candidates and the A/R clock for Build 2 (C3c, C3d first bullet, the Step 2 "past p95" acceptance).** Replaced by
G8 in Step 5.

**X2. Cut: direct streaming of large CSVs from Dropbox on Vercel (8.2 step 3, `REMITWATCH_EMA_STREAM_MAX_MB`, the slow-stream
retry tests).**
- On Vercel a Production file is read only when it is 60 MB or less: a zipped CSV (the two backfill files zip to 12-15 MB) or a
  weekly posted-date pull (about 20 MB).
- A larger raw CSV is recognised from its first 64 KB (one short read, then closed), recorded `waiting` with "Zip it (about
  12-15 MB) or let the server read it", and marked seen.
- A waiting raw CSV is superseded when a zipped member with the same name and uncompressed size is applied.
- The server still reads raw CSVs of any size from disk. Steps 3-4 keep one Production file per Vercel run, detection in the
  next run, and the 8.3 gate (now measured with the zipped files).

**X3. Cut: the EMA patient id to name key bridge in C3e.** The content bridge replaces it (E8).

**X4. Cut: `pccn` as a mapping method** (E7). Kept as a column.

**X5. Deferred to Step 9: C10** (patient-side write-offs on insured lines) and the `ema_months` measure `patient_kind`. Production
has no reason on a $0 row, so a deductible and a denial look the same, and the report needs O8 first.

**X6. Deferred: C11 "by age".** Until the A/R export carries every bucket, the A/R panel shows one patient line ("patient
balances this export lists: $X; it lists only balances with activity in the last 30 days") and, from Step 3, the Production
balances it leaves out. The age table comes back when `aging_window` is NULL.

**X7. Cut: `unit_allowable`** in the ledger and `ema_lines` (E11).

**X8. Simplified: the Upload page preview does not open the ledger.** `preview` reports what the file holds (report, rows, scope,
box, as_of, counts) and which catalog boxes it overlaps. Holds that need the ledger (H3, H4) are decided when the cycle applies
it. The web process never opens the ledger file.

**X9. Kept, contrary to a first reading:** holds H4 and H5. H5 matters because C4 trusts Production boxes as complete: a payer-
filtered first file would turn every other payer's 835 payment into a false "not posted in EMA" item. H4 is a cheap SQL check
once rows are staged.

**X10. Kept, with a stated limit:** the lean keep rule (7.1). A line absent from `ema_lines` means "no EMA evidence here", never
"paid" or "closed". New findings get their lines on the next Part A (the next Production apply), which is also when new
postings arrive. On the server, Part A may also rerun after detection when meta `ema:keep_dirty` is set (finding bills changed).

## 3. Gaps the build must handle

**G1. The A/R window (E1).** Exposure, `aging_window`, accepted bucket columns, `absent_since`, and the panel line "Not in this
export (no activity in 30 days, not denied): $X on N lines" from Production (W4 v2).

**G2. Twin claims and the ASC.**
- A link is `facility = 1` (column on `ema_link_map`, copied to `ema_lines`) when its A/R claim id has the F shape, or when it
  has a twin link (same person, date, code) that maps to an 835 bill without conflict, none of its own insurance checks is an
  835 trace, and the same payer group paid both.
- Facility links are never a map candidate, never count as a payment or answer for a professional finding (C6, C3e), never get
  Build 1 marks, and C5 titles their items "ASC facility claim: ..." (O9).
- W4 shows the facility claims as their own bucket inside the insurance total.

**G3. Line outcome, not claim status (E6).** One per-finding timeline (`ema_checks.line_outcomes`) feeds C6, C2, W2 and the overlays,
so one dollar is read one way everywhere. EMA holds a line under the code it charged: look the line up under the billed code
first, then the original code, then the paid code (a payer's code change is invisible in Production: 0 of 690 changed lines show
a posting on an uncharged code).

**G4. Paid before the finding.** Measured: 218-285 items were already paid by insurance before the finding's remittance (a second
payer's denial after the primary paid, a re-adjudication, CARC B11 or 193). Same payer: the practice contests 17% of these
(24 of 141 validated), so they are never closed. C6 gives the hint "EMA shows this line paid $X by <payer> on <date>, before
this denial: check whether the denial is a duplicate or a second payer's answer". The archive-only version belongs to the detect
unit (section 7).

**G5. Written off on the posting day.**
- Measured: 1,597 items ($184.9K) were adjusted off the day their remittance was posted, 812 later by a person (median 49-72 days).
  Insurance later paid 0.3% of the lines closed at posting and 18.8% of those left open (same payer and reason: 1 of 550 against
  45 of 460). Claim Action is filled on 0.27% of claims, so a same-day write-off is a posting rule, not evidence of work.
- Each C2 mark gets `written_off_when` = `at_posting` (an adjustment posted the same day as the payer's answer on that line) or
  `later`. Chips: "Written off at posting (EMA rule)" and "Written off later".
- `reconcile_summary` adds a policy table: payer group x reason x at posting / later, with items, dollars, still appealable and
  the later-paid share. This feeds owner decision O11. A write-off never closes an item and is never a payment.

**G6. The ASC is a separate payee.** C1 `separate_payee` stays. W4 facility bucket (G2). The docs ask TriZetto about the ASC's 835s
(spec 13).

**G7. Optum and VA naming (E9).** Learned aliases from payer ids, the VA pattern in `payer_group_of`, the codes.py fix, one
re-analysis.

**G8. Unanswered claims from Production (replaces C3c; Step 5).**
- Measured (reverse lens): 352 claims ($106K at charge) have a charge, no insurance posting of any kind and an open balance, and
  are not in the A/R export. 118 of them are paid on the 835s and not posted in EMA (C4's job). 234 ($81.8K) have no answer
  anywhere; 170 are older than the payer's p95. RemitWatch's NO_ANSWER covers 27-58 of the 352.
- Rule: one candidate per link when all of these hold:
  - it has insurance charge rows, no product and no patient-only rows;
  - it has no insurance payment row, no $0 check row and no CARC adjustment since its first charge posting;
  - its balance is above $0;
  - its status class is not `not_sent`, `rejected` or `void`;
  - its bill has no 835 claim (by the map, or by an 835 line on its name key, date and code);
  - due = first charge posting + max(NO_ANSWER_DAYS, the payer group's p95 from first charge to first insurance posting in
    Production; at least 20 links, else 45 days); due is on or before the Production as_of;
  - Production is at most 14 days old, and the payer group has an EMA insurance posting in the 30 days before as_of.
- Item: signal NO_ANSWER. Key: the claim id when `ema_claim_links` knows it, else `link:<n>`. A candidate whose claim id already
  has a candidate from the weekly exports is dropped (the export path wins). The proto-finding carries `heir_keys` so `save()`
  hands status and notes over when the key changes from `link:<n>` to a claim id. Facility links are listed and titled
  "ASC facility claim: ..." (O9).
- Expected on the real files: about 150-200 claims (re-measure).

**G9. $0 answers.** A $0 row with a check number is an answer and a map vote. It is never a payment.

**G10. Second payers.** C5 sizes a second payer's denial at the balance left (E17). "Second payer on file never posted" (measured
619 lines, $22.6K) is a reconcile line and a W4 bucket in Step 5, not items.

**G11. Insurance rows with no claim (unbilled).** Measured: 220 A/R rows ($14,582), 71 of them more than a year past service. They
are a W4 bucket ("held for insurance, no claim created") with a CSV (initials, service date, code, payer, amount), not items: they
have no link and no claim id to open.

**G12. Production is cut by service year.** Postings for 2024 services are in no file. Coverage is by service date (the boxes).
Findings outside coverage get outcome `not_covered`; nothing may read "not in EMA" as evidence for them (3,899 open items have
pre-2025 service).

**G13. Freshness.** Every conclusion drawn from an absence (C4 "not posted", G8 "unanswered", W5 vouching, `answers_through`)
requires Production at most 14 days old (A/R at most 8). Conclusions drawn from a posting that exists (C6, C2, C3e) do not
expire, but carry the as_of.

**G14. Dated history going forward.** Production's status and action are today's values. New store table
`ema_link_events(link, kind, value, first_seen)`: Part A inserts (never deletes, except retention) each (link, 'status' or
'action', value) the first time a Production as_of shows it. W2 dates "worked" from it.

**G15. Sibling lines.** Wherever a line-level EMA figure is used (C2, C5, C6, outcomes), the claim must carry the code once on that
date; otherwise the outcome is `shared_line` and nothing is closed or marked. Measured: 573 items (10.2%) sit on such claims.

**G16. Write-off ramps by code.** Measured: write-offs on one radiation code rose from $66.6K (2025 service) to $217.0K (2026
service so far, $122.7K posted in one quarter), and one payer went from paying 173 of 189 such lines to 13 of 172. Part A adds
an `ema_months` measure `denial_code` (detail `<reason class>|<code>`, denial-type write-offs only) so C7 can name its top codes
by month, and the wiring may raise an alert when a payer group x code's monthly denial write-offs jump (Step 7, optional).

**G17. Keep the three weekly exports (changes O7).** They carry what Production lacks: claim ids, modifiers, remark codes, payer
ids (G7), submission and denial dates, and EMA's handling of each adjustment (WRITE_OFF or IGNORE), which is the only record of
whether a write-off was a posting rule.

## 4. Module boundaries and contracts

### 4.0 Conventions for all three modules
- Python 3.13, `from __future__ import annotations`, plain-English docstrings, short sentences, no em dashes in user-facing text.
- Every function that writes takes `conn` or `store` and commits its own work, as `store.py` and `detect.py` do. `log=print`
  parameters follow `dropbox_sync` and `cloud`.
- Never load a whole EMA table or file into Python. Query in chunks of 500 through a temp key table (7.3). Readers yield rows.
- Money is integer cents, dates are ISO `YYYY-MM-DD` strings, keys are signed int64.
- No cell value, name, MRN, DOB, note, claim id, check number or link number in any log line, exception message or
  `status_reason`. Counts, column names, payer names and codes are fine.
- Each module registers the SQL functions it needs through `ema_reports.register_functions(conn, salt)`.
- Tests use only invented rows (`tests/ema_fixtures.py`, owned by builder 1; later builders put extra helpers in their own test
  files and never edit it). Fixture links use `https://example.ema.test/...` (E15).
- Each builder writes `build/needs_<module>.md` (what existing files must change, function by function) and keeps work files and
  acceptance scripts under `build/<module>/`.

### 4.1 `remitwatch/ema_reports.py` (builder 1): recognition, readers, keys, A/R, catalog, ingest, migration

Owns the store DDL for every EMA table (spec 5.4 as amended in 4.4) and these public names. Signatures are binding; internals are
the builder's.

```python
REPORTS = ("production", "ar_report")
HEADER_SCAN = 25
PEEK_BYTES = 65536
CLAIM_HOST_SUFFIX = "ema.md"                      # tests set "ema.test"
VA_PAYER_IDS = frozenset({"VACCN", "TWVACCN"})
FLAG_PRODUCT, FLAG_POSTED_BEFORE_SERVICE, FLAG_DAMAGED_CHECK, FLAG_IDENTITY_BREAK, FLAG_PATIENT_SIDE = 1, 2, 4, 8, 16
DDL: str                                          # all EMA store tables and indexes (4.4)
STATUS_CLASS: dict[str, str]                      # exact lower-cased EMA words -> class (spec C3a)

class ReportRefused(ValueError):                  # .code in: missing_columns, duplicate_column, two_hosts, two_date_orders,
    code: str                                     #   too_many_rows, zip_in_zip; .message plain words, no cell values
class ReadTimeout(RuntimeError): ...              # a reader passed its deadline (checked every 10,000 rows)

@dataclass(frozen=True)
class Recognized:
    report: str            # 'production' | 'ar_report'
    fmt: str               # 'csv' | 'xlsx'
    header_index: int
    index: dict            # canonical name -> column position
    columns: tuple         # header cells as written
    missing: tuple         # required canonical names not present (non-empty means the file is refused)
    unused: tuple
    delimiter: str | None
    dimension: int | None  # xlsx data rows from the sheet dimension
    member: str | None     # zip member name, when it came from a zip

class Posting(NamedTuple):  # one Production row, keys only; field order is the ledger's column order
    posted: str; svc: str; cpt: str | None; units: float | None; link: int | None; check_no: str | None
    grp: str | None; code: str | None; kind: str | None          # CARC group/code, or EMA's adjustment kind name
    status: str | None; action: str | None
    payer: str | None; plan: str | None; primary_payer: str | None; secondary_payer: str | None; pccn: str | None
    person: int | None; name_key: int | None
    charges: int | None; ins_charges: int | None; ins_paid: int | None; pat_paid: int | None
    contractual: int | None; noncontractual: int | None; pat_adj: int | None; allowed: int | None
    flags: int

def norm(header: str) -> str
def detect_report(columns: Sequence[str]) -> str | None          # for ema_exports.detect_family to call first
def recognize(src: str | bytes, name: str) -> Recognized | None  # a file or its first PEEK_BYTES; None = not a report
def recognize_zip(src: str | bytes, name: str) -> list[Recognized]   # report members; raises ReportRefused('zip_in_zip')
def open_report(src: str | bytes, rec: Recognized) -> ContextManager[BinaryIO]   # file, bytes or zip member, streamed

def money(v) -> int | None
def parse_date(v, order: str = "mdy") -> str | None
def date_order(samples: Iterable) -> str                          # 'mdy' | 'dmy'; raises ReportRefused('two_date_orders')
def link_number(v) -> tuple[int | None, str | None]               # (number, prefix up to '/claim/'); the URL is dropped
def check_number(v) -> tuple[str | None, bool]                    # (text with leading zeros kept, damaged by a spreadsheet)
def code_of(v) -> tuple[str | None, bool]                         # (upper-cased code, is a product)
def reason_of(v) -> tuple[str | None, str | None, str | None]     # (group, code, EMA kind with "Adjustment - " dropped)
def name_tokens(name: str | None) -> str | None

def key_salt(conn) -> bytes                                       # makes meta ema:key_salt on first use
def salt_fingerprint(salt: bytes) -> str                          # sha256(salt)[:16]
def name_key(salt: bytes, name: str | None) -> int | None         # "Last, First" or "First Last"
def name_key_parts(salt: bytes, last: str | None, first: str | None) -> int | None   # the 835 side; same key
def person_key(salt: bytes, name: str | None, dob: str | None) -> int | None
def mrn_key(salt: bytes, mrn: str | None) -> int | None
def note_hash(salt: bytes, note: str | None) -> int | None
def register_functions(conn, salt: bytes | None = None) -> None
    # rw_name_key(last, first), rw_status_class(status), rw_bill(claim_id), rw_payer_group(name), rw_payer_kind(name),
    # rw_check_loose(check) (alphanumerics upper-cased, leading zeros dropped), rw_shape(text) (digits->9, letters->A)

def bill_of_claim(claim_id: str | None) -> str | None             # both shapes; keeps the F (E10)
def is_facility_claim(claim_id: str | None) -> bool

def status_class(status: str | None, has_answer: bool | None = None) -> str
    # pending | denied | rejected | not_sent | answered | paid | settled | gave_up | void | none | unknown
def record_unknown_statuses(conn, words: Iterable[str]) -> None   # meta ema:unknown_status_words

def payer_group_of(conn, ema_payer: str | None) -> tuple[str, str]   # env aliases, learned aliases, VA pattern, codes.normalize_payer
def learn_payer_aliases(conn) -> dict                              # from ema_rows (payer, payer_id); meta ema:payer_aliases_learned

def claim_url_template(conn) -> str | None                        # env REMITWATCH_EMA_CLAIM_URL ('off' -> None), else learned prefix
def learn_claim_prefix(conn, prefixes: Iterable[str]) -> str | None   # validates (4.5 of the spec); raises ReportRefused('two_hosts')

def switch() -> str                                               # REMITWATCH_EMA_REPORTS: 'all' | 'ar' | 'off'; default 'all' on a
def reads(report: str) -> bool                                    #   server, 'ar' when runtime.serverless()

def ensure_schema(conn) -> None                                   # all EMA tables; safe on every start; compact stores included

def file_id_of(src: str | bytes | BinaryIO) -> str               # sha256 in 1 MB chunks

def iter_production(src, rec: Recognized, *, salt: bytes, account: dict, deadline: float | None = None) -> Iterator[Posting]
def iter_ar(src, rec: Recognized, *, salt: bytes, account: dict) -> Iterator[dict]   # ema_ar columns except file_id, as_of

def catalog(conn) -> dict                                         # for GET /api/ema/reports: rows without origin, held, switch, claim URL
def coverage(conn) -> dict                                        # {'production': [boxes of current files], 'production_as_of',
                                                                  #  'ar': {year: {'as_of', 'file_id', 'rows', 'aging_window'}}}
def record_waiting(conn, *, file_id: str, report: str, source_name: str, via: str, origin: dict, reason: str,
                   size: int | None = None) -> None
def waiting(conn, report: str | None = None) -> list[dict]        # entries to read, with their origins, oldest first
def ingest_file(store, path: str, rec: Recognized | None = None, *, via: str, origin: dict | None = None, force: bool = False,
                ledger=None, deadline: float | None = None, log=print) -> dict
def apply_ar(store, path: str, rec: Recognized, *, file_id: str, via: str, origin: dict | None = None, force: bool = False,
             log=print) -> dict
def preview(conn, path: str, rec: Recognized | None = None) -> dict   # X8: never opens the ledger
def decide(conn, file_id: str, decision: str, user: str) -> dict  # 'apply' | 'discard'; returns {'requeue': origin} on apply
def migrate_misread(store, *, serverless: bool, log=print) -> dict   # E4; sets meta ema:migrated_v1 when done
def confirm_misread(store, sha256: str, rec: Recognized | None) -> bool   # serverless second half of E4, called on fetch
```

`account` keys (both readers fill what applies; counts and dates only): `rows_read`, `rows_kept`, `left` ({blank, total, no_date}),
`bad_amounts`, `bad_dates`, `damaged_checks`, `identity_breaks`, `hosts` (a count), `svc_lo`, `svc_hi`, `posted_lo`, `posted_hi`,
`as_of`, `date_order`; A/R also `aging_window`, `buckets`. The reader keeps no per-line sets: distinct links, distinct payers and
`charge_line_share` are computed by the ledger from its staging table.

`ingest_file` result (also what `ema_ledger.apply_production_file` returns for the Production part):
`{'status': 'current'|'superseded'|'held'|'refused'|'waiting'|'already'|'not_report', 'report', 'file_id',
'reason', 'rows_read', 'rows_kept', 'rows_applied', 'live_rows', 'scope', 'box', 'as_of', 'replaced': [(file_id, status)],
'account'}`. `ingest_file` is the only writer of `ema_report_files`, `files` (kind `ema:production` / `ema:ar`) and `ema_sources`
for reports. For Production it calls `ema_ledger.apply_production_file` lazily (inside the function); when `ema_ledger` cannot be
imported, the switch does not read Production, or no ledger is given, it records `waiting` (Step 0 behaviour). `ledger` may be a
`Ledger` or a zero-argument callable that opens one (the serverless run opens the ledger only when a Production file is present).
For a zip, `ingest_file` returns `{'status': 'zip', 'members': [result, ...]}`, one result per report member (each member's
file_id is the SHA-256 of its own bytes). Files may be applied in any order: the box invariant gives the same ledger whatever
the order, so the router does not sort them.

### 4.2 `remitwatch/ema_ledger.py` (builder 2): the ledger, apply and holds, retention, refresh, the map

```python
LEDGER_SCHEMA = "1"
LEDGER_FILE = "ema_ledger.sqlite"
STATE_NAME = "ema_ledger.sqlite.gz"
ATTEMPTS_NAME = "ema_attempts.json"
RETAIN_DAYS = 1100

@dataclass(frozen=True)
class Box:
    file_id: str; scope: str; svc_lo: str; svc_hi: str; posted_lo: str; posted_hi: str; as_of: str; order: tuple

@dataclass
class FileMeta:
    file_id: str; source_name: str; via: str; scope: str | None; svc_lo: str | None; svc_hi: str | None
    posted_lo: str | None; posted_hi: str | None; as_of: str | None; created_at: str | None; received_at: str
    rows_read: int = 0; rows_kept: int = 0

def ledger_path(db_path: str) -> str                    # REMITWATCH_EMA_LEDGER, else <store dir>/ema_ledger.sqlite (0600, WAL)
def scope_of(svc_lo: str, posted_lo: str) -> str        # 'posted' when posted_lo - svc_lo > 45 days, else 'service'

class Ledger:
    path: str
    conn: sqlite3.Connection
    @classmethod
    def open(cls, path: str, *, readonly: bool = False, scratch: bool = False) -> "Ledger"   # scratch: journal and sync off
    def seq(self) -> int
    def salt_fp(self) -> str | None
    def bind_salt(self, salt_fp: str) -> list[str]       # set on an empty ledger; on a mismatch empty it, return file_ids to re-read
    def files(self) -> list[dict]
    def boxes(self, statuses: tuple = ("current", "partly_replaced")) -> list[Box]
    def stage(self, rows: Iterable[Posting], *, deadline: float | None = None) -> dict
        # into a separate attached file next to the ledger; returns links, payers, charge_line_share, identity_breaks, rows
    def holds(self, meta: FileMeta, stats: dict, *, serverless: bool, fmt: str = "csv",
              dimension: int | None = None) -> list[tuple[str, str]]   # H1-H6 as (code, plain message)
    def commit(self, meta: FileMeta) -> dict             # box replacement (6.2) in one transaction; seq + 1
    def drop_stage(self) -> None
    def retain(self, days: int = RETAIN_DAYS) -> int
    def backup(self, dest: str) -> None                  # SQLite online backup
    def close(self) -> None

def apply_production_file(store, path: str, rec, *, file_id: str, via: str, origin: dict | None = None, force: bool = False,
                          ledger: "Ledger", deadline: float | None = None, serverless: bool = False, log=print) -> dict
def sync_catalog(store, ledger: "Ledger") -> int        # catalog statuses from ledger_files (crash repair, 8.2.4)
def refresh(store, ledger: "Ledger | None" = None, *, parts: str = "auto", log=print) -> dict
    # parts: 'auto' (A when ledger seq/rev differs from meta or retention ran, then B), 'A', 'B', 'AB'; Part A needs a ledger
def map_links(conn) -> dict                              # Part B step 1 (E7); counts by method, conflicts
def mark_facility(conn) -> int                           # G2
def keep_rule_sql() -> str                               # the lean keep rule as one SQL condition over ema_lines (tests read it)
def ledger_postings(ledger: "Ledger", links: Sequence[int], *, limit: int = 500) -> list[dict]   # claim view, server only; no keys

class LedgerHandle:                                      # serverless only
    def __init__(self, state, tmp_dir: str | None = None)
    def open(self) -> "Ledger"                           # downloads STATE_NAME if present, unpacks to tmp; else a new scratch ledger
    def save(self) -> str                                # gzip level 1, state.put_chunks; returns the revision
    def discard(self) -> None
    def note_attempt(self, file_id: str) -> int          # writes ATTEMPTS_NAME before a read starts; returns the count
    def clear_attempt(self, file_id: str) -> None
```

Part A writes `ema_lines`, `ema_line_payments` ($0 rows included, `ins_paid = 0`), `ema_line_reasons`, `ema_checks` (all columns
but `cause`, `pair_trace`), `ema_months` (all measures but `no835`) and `ema_link_events`, and sets meta `ema:ledger_seq` (server)
or `ema:ledger_rev` (serverless). Part B writes `ema_link_map` (with `facility`), the in-835 marks (`in_835`, `trace_key`,
`ema_checks.match`), `ins_paid_no835`, payer groups (through `rw_payer_group`), then prunes by the keep rule; it clears
`ema:map_dirty`. Neither part writes findings. `ema_ledger` never imports `ema_checks`.

/tmp budget on Vercel: ledger (about 140 MB at two years) + staging file (about 70 MB for a year file) + its gzip must stay under
400 MB; the staging file and the scratch ledger are deleted before the dashboard copy is built.

### 4.3 `remitwatch/ema_checks.py` (builder 3): statuses, turnaround and lag, C1-C9, W2-W4, the reconcile summary

```python
from .ema_reports import status_class                    # re-export: the spec's ema_checks.status_class
TURNAROUND_KEY = "ema:turnaround"; LAG_KEY = "ema:posting_lag"; RECONCILE_KEY = "ema:reconcile"; AR_ACCOUNTED_KEY = "ema:ar_accounted"
OUTCOMES = ("paid_after", "paid_after_other", "paid_after_partial", "paid_before", "action", "written_off_at_posting",
            "written_off_later", "contractual_at_posting", "contractual_later", "patient", "open", "closed_unexplained",
            "shared_line", "not_found", "not_covered")

def turnaround(conn) -> dict          # {group: {'svc_p95', 'charge_p95', 'n'}}: 835 service -> first answer; Production first charge
                                      #   -> first insurance posting; groups with >= 20, else 45 days; written to TURNAROUND_KEY
def posting_lag(conn) -> dict         # {group: p95 days}, default 8; LAG_KEY
def classify_checks(conn) -> dict     # C1 causes into ema_checks.cause / pair_trace and ema_months 'no835'; runs before C4 and C6
def claim_statuses(conn, *, claim_ids: Iterable[str] = (), links: Iterable[int] = ()) -> dict
                                      # newest status by as_of across A/R, Production and Claim & Claim Status (C3a)
def answers_through(conn, payer_group: str, newest_835: str | None) -> str | None   # C3d, Production part only
def claim_answers(conn, claims: list[dict]) -> dict       # C3e; claims: {'claim_id', 'sent', 'lines': [(svc, cpt, charge_cents)],
                                                           #   'payer_group'}; returns {claim_id: {'answered', 'how', 'on', 'paid_cents'}}
def not_sent(conn, *, today: str) -> list[dict]            # C3b proto-findings (NOT_SENT)
def unanswered_claims(conn, *, today: str, skip_claim_ids: set, rate) -> list[dict]   # G8 proto-findings (NO_ANSWER)
def line_outcomes(conn, findings: Iterable[dict], *, code_once) -> dict[str, dict]   # G3; code_once(finding) -> bool
def paid_in_ema(conn, findings: Iterable[dict], outcomes: dict, *, closes: bool) -> dict[str, dict]   # C6
def written_off_in_ema(conn, findings: Iterable[dict], outcomes: dict) -> dict[str, dict]              # C2
def ema_denials(conn, *, today: str, rate, ratio, has_835_finding) -> list[dict]   # C5 proto-findings (EMA_DENIED)
def unposted_remits(conn, *, today: str) -> list[dict]    # C4 proto-findings (REMIT_NOT_POSTED) and posting differences
def denial_as_contractual(conn) -> list[dict]             # C7 proto-findings (EMA_DENIAL_AS_CONTRACTUAL)
def overlays(conn, findings: Iterable[dict], outcomes: dict) -> dict[str, dict]   # W2 + ema_* display columns (5.5 + 4.4)
def unworked(conn, findings: Iterable[dict], *, data_through: str) -> dict        # W3
def credits(conn) -> dict                                 # C8 (E3)
def ar_accounted(conn, findings: Iterable[dict]) -> dict  # W4; buckets add up to the insurance exposure total; AR_ACCOUNTED_KEY
def reconcile_summary(conn) -> dict                       # RECONCILE_KEY: C1, C4 summary, C7, C8, C9, G5 policy table, W4, freshness
def data_health(conn) -> list[dict]                       # the ema_* items of 11 "Data health", plus remits_missing and credits
def claim_block(conn, *, claim_id: str | None = None, bill: str | None = None, links: Sequence[int] = (), ledger=None) -> dict
def ar_list(conn, bucket: str, page: int = 1) -> dict     # /api/ema/ar
def checks_csv(conn, *, cause: str | None = None, date_from: str | None = None, date_to: str | None = None) -> Iterator[list]
def unposted_csv(conn) -> Iterator[list]
def credits_csv(conn) -> Iterator[list]
```

Callables passed in from the Detector (so `ema_checks` never imports `detect`): `rate(payer_group, code, year) -> int | None` (the
usual rate), `ratio(payer_group, code) -> float | None` (paid/charge over 12 months, at least 10 lines), `code_once(finding)`,
`has_835_finding(bill, svc, code) -> bool`.

**Proto-finding** (every list-returning check): a dict the detect wiring passes straight to `Detector._emit`:
`{'signal', 'id_signal' (optional), 'key_parts': tuple, 'category', 'severity', 'actionable', 'title', 'claim': {...},
'expected_cents', 'shortfall_cents', 'carc', 'rarc', 'evidence': dict, 'appeal_deadline', 'deadline_basis', 'hint',
'source': 'ema', 'ema_link', 'heir_keys': [tuple]}`, where `claim` holds the base fields `_emit` reads
(`patient_control_number`, `payer_name`, `payer_group`, `payer_kind`, `service_date`, `year`, `procedure_code`, `charge_cents`,
and for C4 `transaction_key`, `payment_date`). The wiring calls
`_emit(signal, category, severity, actionable, title, None, expected, shortfall, carc, rarc, evidence, key_parts=..., claim=...,
id_signal=...)` and then sets `appeal_deadline`, `deadline_basis`, `hint`, `source` and `ema_link` on the stored finding. Keys:
NOT_SENT and NO_ANSWER use `id_signal='EMA_OPEN_CLAIM'` with the claim id or `link:<n>`; EMA_DENIED `(link, class)`;
REMIT_NOT_POSTED `(transaction_key,)`; EMA_DENIAL_AS_CONTRACTUAL `(payer_group, reason, year)`. Evidence never holds a name.

**Outcome** (`line_outcomes` value per finding id): `{'link', 'bill', 'as_of', 'covered', 'facility', 'shared_line', 'anchor',
'anchor_basis' ('own_posting' | 'remit_plus_lag'), 'outcome', 'outcome_on', 'paid_after_same', 'paid_after_other',
'paid_after_835', 'paid_before', 'first_paid_after' {date, check_tail, payer, cents}, 'written_off', 'written_off_kind',
'written_off_when', 'written_off_on', 'reasons' [[grp, code, cents, posted]], 'patient_after', 'balance', 'status',
'status_class', 'action'}`. The anchor is the day EMA posted the finding's own trace on that line (found for about 95% of items),
else the 835 payment date plus the group's median lag. "At posting" means the anchor's calendar day.
`paid_in_ema` returns `{fid: {'action': 'close'|'hint_other_payer'|'hint_partial'|'hint_paid_before', 'paid_cents', 'date',
'check_tail', 'payer', 'note', 'used': (check, date, link)}}`; the wiring calls `_mark_paid` for 'close' when
`REMITWATCH_EMA_CLOSES` is on, else sets the hint. `written_off_in_ema` returns `{fid: {'written_off_cents', 'written_off_on',
'written_off_source', 'written_off_kind', 'written_off_when'}}` (cents = max(ERA-lines figure, min(net, shortfall)), never a sum).

### 4.4 Store and meta contract

Store tables (DDL in `ema_reports.DDL`; spec 5.4 plus these changes):
- `ema_report_files`: as specified; `account` JSON also holds `aging_window` and `buckets` for A/R files.
- `ema_ar`: add `exposure_cents INTEGER`, `facility INTEGER`.
- `ema_ar_items`: `gone_since` renamed `absent_since`.
- `ema_lines`: drop `unit_allowable`; add `facility INTEGER`, `first_charge_posted TEXT`, `first_ins_posting TEXT` (first insurance
  payment, $0 check row or CARC adjustment), `ins_answer_rows INTEGER`.
- `ema_line_payments`: holds $0 check rows (`ins_paid = 0`); '' for a missing check number or payer (E14).
- `ema_link_map`: add `facility INTEGER DEFAULT 0`; `method` is one of 'ar', 'payment', 'patient_day' (no 'pccn').
- New: `CREATE TABLE IF NOT EXISTS ema_link_events (link INTEGER, kind TEXT, value TEXT, first_seen TEXT,
  PRIMARY KEY (link, kind, value)) WITHOUT ROWID;` (G14).

New finding columns (wiring adds them to `detect.EXTRA_FINDING_COLS` and `api.FINDING_COLS`): the spec's list (5.5) plus
`written_off_when`, `ema_outcome`, `ema_outcome_on`.

| Store table or meta key | Written by | Read by |
|---|---|---|
| `ema_report_files`, `files`/`ema_sources` rows of reports | `ema_reports.ingest_file`, `record_waiting`, `decide`, `migrate_misread`; statuses also by `ema_ledger.sync_catalog` | api, `coverage`, checks |
| `ema_ar`, `ema_ar_items`, `ema_claim_links` | `ema_reports.apply_ar` | ledger Part B, checks |
| `ema_lines`, `ema_line_payments`, `ema_line_reasons`, `ema_checks` (less cause), `ema_months` (less no835), `ema_link_events` | `ema_ledger` Part A | checks |
| `ema_link_map`, in-835 marks, `facility`, payer groups, pruning | `ema_ledger` Part B | checks |
| `ema_checks.cause`, `pair_trace`, `ema_months` no835 | `ema_checks.classify_checks` | reconcile, api |
| `ema:key_salt` (never served) | `ema_reports.key_salt` | readers, ledger salt check |
| `ema:claim_url_prefix` | `ema_reports.learn_claim_prefix` | api |
| `ema:payer_aliases_learned`, `ema:unknown_status_words`, `ema:migrated_v1` | `ema_reports` | `payer_group_of`, data health, wiring |
| `ema:ledger_seq`, `ema:ledger_rev`, `ema:map_dirty`, `ema:keep_dirty` | `ema_ledger` (keep_dirty: detect wiring) | wiring |
| `ema:turnaround`, `ema:posting_lag`, `ema:reconcile`, `ema:ar_accounted` | `ema_checks` | api, brief, items |

### 4.5 Detection order (for the wiring phase; replaces spec 10)
1. `build_links` (as today).
2. `ema_ledger.refresh(store, None, parts='B')` when meta `ema:map_dirty` is set (after 835s or an A/R arrived).
3. `ema_checks.classify_checks(conn)`, `turnaround(conn)`, `posting_lag(conn)`.
4. `load`, baselines, line rules, claim rules.
5. `run_ema_open_claims`: the status dictionary, export candidates with `claim_answers` and `answers_through`, then `not_sent`,
   then `unanswered_claims` (skipping claim ids the exports already listed).
6. `ema_denials` (C5).
7. Aggregate rules, plus `denial_as_contractual` (C7).
8. Second rates.
9. `resolve_elsewhere` (archive branches first), then `line_outcomes` once, then `paid_in_ema` (C6).
10. `unposted_remits` (C4).
11. `written_off_in_ema` (C2) and `overlays`.
12. `save`, with `ema_heirs` and `heir_keys`.
13. `reconcile_summary`, `ar_accounted`.

`DETECT_RULES` and `_rules_hash` change as the spec says (2.7-2.11; the hash includes the three modules).

### 4.6 What each needs file must cover
- `needs_ema_reports.md`: `ema_exports.detect_family` (reports first), `iter_export_paths` (.csv/.tsv/.zip only when a report),
  `iter_rows`/`read_export_detail` refuse report families; `Store.__init__` and `Store.from_bytes` call `ensure_schema`;
  `detect.ensure_schema` calls it too; `dropbox_sync` (`WANTED_EXT`, `wanted()` keeps the backfill zip out, the router sends reports
  to `ingest_file`, old families over 60 MB refused, unknown layouts recorded `ema:unknown`/`not_read`, an `origins` argument,
  `download_chunks(..., chunk, timeout=(10, 30))`); `cloud` (`_ingest_batch` origin element, the X2 waiting path, re-reading
  waiting entries by Dropbox id, `confirm_misread` on fetch, `SERVING_TABLES` with key columns excluded, `SERVING_BUILD` "7",
  the meta filter for `ema:key_salt`); `uploads` (sniff, recognise, `preview`, the kept copy, wording by catalog status, held
  decisions); `pipeline` (one `migrate_misread` per host, waiting entries); `codes.py` (VA rule above OPTUM, VACAA); api catalog
  and decision routes; `.env.example`.
- `needs_ema_ledger.md`: `pipeline` (open the ledger at `ledger_path` once per cycle, apply every Production file of the cycle,
  `refresh` before detection, backups); `cloud` (`LedgerHandle` on apply runs only; `note_attempt` before the read; save order ledger, meta rev,
  store, dashboard copy, cursor; one Production file per run; `more=True`; the repair path); `detect.run` step 2; `cli ema-reports`
  (`--status`, `--summarize`, `--reread`, `--decide`); `backup`, `import-cloud`, `compare-cloud` (keys excluded from compares);
  the claim view's server postings.
- `needs_ema_checks.md`: every detect.py step in 4.5; `EMA_CLAIM_SIGNALS` + NOT_SENT; `actions.NEVER_HIDDEN` + NOT_SENT and
  REMIT_NOT_POSTED; `insights` (the `post` work type, labels, EMA_DENIED treated as CLAIM_DENIED_UNPAID); api reconcile, CSV, A/R
  list, `ema=` filters, finding and claim `ema` blocks, `/api/brief`; alerts `REMITS_MISSING`, `EMA_STALE`; data health; the
  weekly email; W5 in `appeals_send`; the pages, with the outcome shown before the claim status (E6).

### 4.7 Tests each builder owns
- Builder 1: `tests/ema_fixtures.py` (complete for all three builders: columns, row builders, CSV/xlsx/zip writers including a
  14-character F claim id, `big_production_csv`), `tests/test_ema_reports.py` (spec 12 items 1-10, plus: exposure never sums the
  two amounts; `aging_window`; the 23-row-style genuine export survives the migration; a conversion error never shows its cell
  text; `bill_of_claim` on both shapes; learned VA alias; the repository scan for `.ema.md` hosts).
- Builder 2: `tests/test_ema_ledger.py` (spec 12 ledger items, plus: $0 rows vote; a twin facility link never wins patient_day;
  a charge mismatch leaves a bill unmapped; `ema_link_events` keeps the first as_of across refreshes; '' keys), and
  `tests/test_ema_vercel.py` for `LedgerHandle` with `LocalState` (save/open round trip, attempts written before a read, a
  run "killed" after `note_attempt` counts on the next open, the staging file removed).
- Builder 3: `tests/test_ema_checks.py` (spec 12 checks items, amended: C6 ignores later 835-trace payments; status alone never
  answers or closes; written_off_when; paid-before gives a hint only; G8 candidates and their key hand-over; C8 counts negative
  denial amounts; W4 adds up with exposure; facility links excluded everywhere).
- The cycle-level parity tests (`run_cloud_cycle` against `pipeline.run_cycle`) belong to the wiring phase.

## 5. Steps and acceptance, amended

| Step | Ships | Acceptance on the real files |
|---|---|---|
| 0 | Guard and migration (E4) | the four files recognised (two `production`, two `ar_report`); 0 report rows in `ema_rows`; on the store copy the 23-row genuine export is not touched |
| 1 | A/R in (G1, E10) | 9,653 rows (9,411 + 242); 4,229 + 61 claim ids; Current A/R $1,115,513 (2026) and $3,218 (2025); denial-only exposure $87,370 and $91,388; 77 rows with both amounts, equal; F claims 120 ids, 143 rows, $96,233; insurance rows with no claim id 220 ($14,582); `aging_window` = 30 on both files |
| 2 | Status dictionary, NOT_SENT, credits (E3), VA (E9), W4 v1. No Build 2 from the A/R (X1) | NOT_SENT about 60 lines due ($7.1K), 39 over 30 days, on the service-date clock (59 of the 73 not-sent rows have no aging date); credits: insurance -$22,979, Medicare primary over 60 days 20 rows -$16,752 (data health `credits` not ok), patient -$34,621; W4 v1 total $1,284,522 (Current $1,105,763 + denial-only $178,759) |
| 3 | Production on the server (E7, G2, G14) | map at least 95% of covered 2025+ bills, with the patient_day share reported; 0 facility links mapped to a professional bill; lean lines about 74.5K of 204K (within 10%) |
| 4 | Production on Vercel from files of 60 MB or less (X2) | gate of 8.3 with the zipped files; an attempt is recorded before each read |
| 5 | Outcomes (G3), C6 (E5), C2 with `written_off_when` (G5), C3 v2 with the content bridge (E8) and Production unanswered claims (G8), W5 | C6 roughly 180-230 closures (upper bound 227 before the same-payer test), about 0 downcoding or underpayment (re-measure; report by category); C2 about 900 items ($83K) inside the deadline, split at posting / later; C3e NO_ANSWER down by about 78-99 of the 124 checkable; G8 about 150-200 claims |
| 6 | C1 (with learned VA), C4 (E13) | C1 $2.74M within 2% (Jan 2025-Sep 2026); each cause within 15% (separate_payee about $0.92-0.95M, VA about $849K, no_era about $0.26-0.29M, feed_gap about $0.61-0.66M); C4 re-measured after misnumbered pairing, expected about $11.5-25K (the lenses' $11.5-20.6K), with any remaining self-funded block explained |
| 7 | C5 (E17), C7, C9 (aggregate only); C10 deferred (X5) | C5 about 460 lines (about $41K) inside the window, and 0 items on a link whose patient-day key has an 835 line; C7 $459K in charges |
| 8 | W2 (G14 first, A/R diffs second), W3, W4 v2 (G1 line, G10, G11 buckets), Today, Progress, email | W4 buckets add up exactly to the insurance exposure total |
| 9 | Later: inbox and machine upload, ASC fee audit, C10, secondary-never-posted items, the C11 age table | - |

Re-run every acceptance with and without the detect unit's archive-only rules (section 7): C6, C3e and C2 move when those rules
close items first.

## 6. Owner decisions, amended and new

| # | Decision | Default |
|---|---|---|
| O2 (amended) | Vercel: read Production from zipped files (zip the two backfill CSVs once), keep it on `ar`, or retire it | `ar` until the gate passes; then zipped files only |
| O3 (amended) | Confirm that the EMA payer name the exports tag with VA network payer ids is VA Community Care | learned from payer ids (E9); the env alias overrides |
| O7 (amended) | Keep all three weekly exports | keep them: they carry claim ids, modifiers, remark codes, payer ids and EMA's adjustment handling that Production lacks (G17) |
| O11 (new) | For denials EMA writes off the day they post (by payer and reason): pursue, or accept by policy | list them with `written_off_when = at_posting` and the policy table (G5); hide nothing |
| O12 (new) | Ask EMA for the A/R with every aging bucket (or Total A/R) and no 30-day limit, and for Claim ID, Bill Item Identifier and Modifiers on Production | the code reads either; the routine (13.1) asks for them |
| O13 (new) | The Medicare primary credits over 60 days (one visit looks like an unposted reversal) | listed as not ok on Data health; a person decides |

O1, O4, O5, O6, O8, O9 and O10 stand as written.

## 7. Not in these three modules (for the detect unit and others)
- Archive-only rule gaps that need no EMA data (why-missed lens: 1,859 items across all years; 573 of the EMA-paid items were
  knowable from the 835s): same payer paid before (reclassify, do not close), another payer paid first, units, the later-payment
  check for short payments, same-day remittances, second payer under half; folding repeat denials of one service (413 extra items).
- Rules for PR-group denials that are not deductible, coinsurance or copay (PR-288, PR-27, PR-49, PR-204, PR-200) and CO-147.
- Retiring items past every window (6,326 of 9,539 dated items).
- Hide rules at payer x code x modifier x reason grain (the modifier-57 pattern differs by payer and is never all or nothing).
- Expected amounts for drugs and skin substitutes (33 open items carry $116.8K of EMA balance and are valued at $0).

## 8. Owner request during the build (2026-11-22): flag EMA's automatic write-offs ("looks resolved, not worked")

Why. The payment-month skeptic found, in EMA's own ERA-lines export, that EMA's posting rules write off nearly every CO
adjustment the day the remittance posts (CO codes: 9,051 WRITE_OFF, 208 IGNORE, 1 TRANSFER; on 37 of 38 auto-closed items
checked the handling was WRITE_OFF). Those lines then sit on a claim EMA calls Resolved, so the biller reads them as worked.
Nobody worked them and the payer paid nothing. The owner asked that RemitWatch say so plainly on every such item.

Rule (ema_checks; one outcome per item line, computed from postings, never from claim status):
- `ema_outcome = 'auto_written_off'` when ALL hold: (1) the payer's answer on the line is a denial: a denial-type CARC (not
  CO-45, CO-253, CO-24, CO-59 alone, PR deductible/coinsurance/copay, OA-23) with the line paid $0, or a denial-type CARC
  taking a separate amount; (2) EMA wrote that amount off (non-contractual or contractual adjustment) on the posting day of
  the remittance that carried the denial (same posting date; adjustment rows carry no check number, so match on claim
  link/line + payer + posting day + CARC or amount); (3) no later insurance payment, patient payment or later adjustment
  explains the line.
- Evidence level: `rule_proven` when the weekly ERA-lines export (ema_rows era_lines) for that remittance shows EMA's
  handling WRITE_OFF for that adjustment; `posting_day` (inferred) otherwise. Never claim "rule" without the export.
- Keep it distinct from: `written_off_later` (a later adjustment on a day with no remittance posted: likely a person; show
  the date and the EMA kind/CARC), `paid_later`, `paid_before`, `patient`, `open`, `written_off_by_policy` (below).
- Expose per item: outcome, evidence level, CARC(s), amount written off, posting date, still-appealable flag and deadline
  (RemitWatch's own window, not EMA's), and EMA claim status (shown only as context: "EMA calls the claim Resolved/Paid
  because other lines were paid").
- Never close or hide an item because EMA's claim status is Resolved. An auto write-off keeps the item open on the
  worklist unless a person, or the policy table, decides.

Policy table (owner decision O11, now concrete): patterns = payer group x code (CPT/HCPCS) x modifier (when the weekly
exports give it) x CARC. Each pattern is `pursue` (default) or `accept` (set by a person; record who and when; owner
password not required unless the existing hide rules require it). Items matching an `accept` pattern show
`written_off_by_policy` and leave the work queue but stay findable. Ship a pattern summary (count, dollars, still
appealable, later-paid rate where the archive shows it) so about 20 patterns covering about 1,230 of the ~1,600 same-day
items can be reviewed in one sitting.

Wording for the wiring phase (pages): chip "Auto write-off" on the row; in the item panel: "EMA shows this claim as
Resolved, but nobody worked this line. EMA's posting rule wrote off the denial (<CARC>, $<amount>) on <date>, the day the
payer's remittance was posted. The payer paid nothing." then "You can still appeal until <date>." or "The appeal window
closed on <date>." and the evidence line ("Recorded by EMA's ERA export" or "Inferred from the posting date"). A Denials
filter "Auto write-offs still appealable", sorted by dollars then deadline, and a count on Today. Plain English, no em
dashes, no jargon like "false positive".

Acceptance (real files): count and dollars of auto_written_off items by evidence level, by payer and CARC; the top 20
patterns; the still-appealable subset; spot-read 25 de-identified items to confirm each is a denial written off on its
posting day with nothing after it.

### 8.1 Two more "looks handled, was not" labels (from the service-month skeptic, May 2026 service)
- `written_off_while_pending`: EMA wrote off, on the posting day, an amount the payer marked as pending or correctable
  rather than final: OA-133 (claim pending further review) and CO-16 (missing information; correct and resubmit), plus
  any CARC the codes table marks as 'pending' or 'resubmit'. Wording: "EMA wrote this off the day it posted, but the
  payer had not finished: it asked for <information/review>. This can usually be corrected and sent again." Measured: part
  of 68 non-G2211 recoverable lines ($17.5K) among 500 May denials closed at posting.
- `lost_to_timely_filing`: a CO-29 (or EMA timely-filing kind) write-off on a line whose earlier payer answer was a
  different denial (CO-16, CO-97, CO-236 and others). Wording: "This was written off for late filing. The payer first
  denied it for <CARC>; it ran out of time before anyone fixed it." Report-only (the money is usually gone); count it on
  Progress as a process loss. Measured: 9 such May denials ($918), 86 items in all 2025-2026 ($19.2K in EMA).
- Precedence on one line: paid_later > patient > lost_to_timely_filing > written_off_while_pending > auto_written_off >
  written_off_later > open. Strict reading everywhere (the skeptic's rule): a closing at posting and a CO-29 write-off are
  never counted as work.

### 8.2 Seed for the policy table (aggregate evidence; the owner decides)
From the why-missed lens (Table 8, 2025+ service; 'paid later' = share of all zero-paid findings of the pattern, any year,
later closed as paid). Ship the pattern summary with this column so the decision is informed:
- Likely PURSUE (often recovered): Humana MA 99214 CO-16 (39%), 99213 CO-16 (30%), 99213 CO-97 (45%), 99214 CO-97 (20%);
  Anthem/BCBS 99213 CO-97 (24%), 99214 CO-97 (33%), 99212 CO-97 (29%), J3301 CO-16 (32%); Anthem MA 99212/99213 CO-97 (31%/56%).
- Likely ACCEPT for past claims (0-1% ever paid; front-end fix instead): G6001 at Anthem MA/UHC MA/UHC with CO-197, CO-272,
  CO-210 (authorization; ~700 items, ~$88K listed); Anthem 77436 CO-97 (0%, $20K); Humana MA 77600 CO-16 (0%); Humana MA
  96904 CO-96 (non-covered, 0%); Anthem 99203 CO-186 (0%).
Default stays 'pursue' for every pattern until a person sets it; the summary shows these recovery rates beside each row.
- CORRECTION to 8.2 (measured on the 835 archive, 2026-11-22): the radiation authorization denials are a backlog, not a
  recurring stream. Zero-paid SRT-family lines with an authorization CARC (197/272/210/B7): 2025 about 40-80 a month
  (G6001); 2026 January 104, February 70, March 33 (77436 and 77437, the 2026 codes that replaced 77401 and G6001, and
  77600), May 11, none after. The policy decision covers the old items only; do not describe it as an ongoing leak.

## 9. Owner request (2026-11-22): every item shows its story, its verdict and its next step, in the app

The owner asked that the practice-manager trace be built into RemitWatch, not done by hand. For every item (and every claim
in the claim view), computed from postings and remittances, never from EMA claim status alone:

9.1 The story (evidence trace). One chronological list of every event on the line (and the claim where the line has none),
each tagged with its source: the charge (Production; charge-line export units, modifiers, location); the submission
(claim-status export: submitted date and suffix; 837 copy when present); acknowledgments (999/277CA when present); each 835
adjudication (payer, paid, allowed, every CARC with group, amount and meaning, every RARC with meaning, claim status code,
reversal pairs, paid code vs billed code, units); each EMA posting (payment: amount, whether the check has an 835 on file;
adjustment: amount, reason, kind, "EMA's posting rule wrote this off" only when the ERA export shows WRITE_OFF, posted on the
remittance's posting day or N days later with no remittance that day; patient payment or adjustment; void or re-create); the
A/R row as of its pull (balance, denial amount, status, action, a note exists, and the 30-day window caveat); RemitWatch's own
events (finding raised, statuses, faxes, notes). End with a balance line that reconciles to the cent: charge = insurer paid
+ patient paid + contractual + written off + open, plus or minus credits; if it does not reconcile, say so and show the gap.
Dates are shown in the app (behind the login); the names switch governs patient details as today.

9.2 The verdict. One plain sentence naming the state and the decisive story rows (highlighted), with its strength (proven:
two sources agree; one source; inferred, with the rule named). Examples: "Paid later: $84.10 by Humana on a check with no
835 (row 6)." / "Looks resolved in EMA, not worked: EMA's posting rule wrote off CO-97 $112.00 the day the remittance posted
(row 4). The payer paid nothing." / "Listed twice: the same service as item <id>." / "The payment on this day belongs to the
surgery center's facility claim, not this line (rows 5-6)." / "Unanswered: no answer from any payer in 61 days; this payer
usually answers in 18." / "The 835 paid $X (row 3), but EMA never posted it." Every automatic closure or relabel keeps its
verdict and rows on the closed item, with Reopen.

9.3 The next step. What to do, who, by when (deadline and basis), and the expected value, with one click wherever the work can
be done in RemitWatch: Appeal: [Build appeal packet], showing what it needs (the visit note of the date of service:
[Attach note] / [Find in ModMed] / [Upload]), then [Fax to <payer> appeals] only to a confirmed number, preview first.
Corrected claim: the exact change ("add modifier 25 to 99214 on line 1; resubmit as a corrected claim, frequency 7, with the
original claim number") with [Copy instructions] and [Mark sent]. Call or portal check: the exact question and the facts to
have ready (payer claim number, date of service, codes, billed, denial codes) with [Log outcome]. A payment EMA missed: what
to post (check, amount, line) with [Mark posted]. Bill the secondary or the patient: amount and basis with [Mark billed].
Write-off: only through the owner's policy table ([Accept pattern], owner-gated) or a person's decision with a reason
([Let go] with a note). Nothing to do: say so, with the proof rows. Work outside RemitWatch is spelled out in steps; never
"review".

9.4 Missed claims become items with the same story, verdict and next step: EMA-only denials (C5), unanswered claims found
from Production (G8), 835 payments EMA never posted (C4).

9.5 Acceptance. The practice-manager walkthrough (${S}/pm/worksheet.json, about 66 independently verified cases): for every
case, the app's verdict and next step must match the verified truth; a mismatch is a bug to fix before shipping. Screenshot
each case (local copy, names off) for the owner.

Module contract (for ema_checks and the wiring): expose item_story(conn, finding_id or claim key) -> ordered rows with source,
kind, amounts, codes and meanings, decisive flag; item_verdict(...) -> {state, sentence, rows, strength}; next_step(...) ->
{action, who, deadline, basis, expected_value, buttons, instructions}. Pure functions over the store and ledger, used by the
API, the pages and the tests alike.

## 10. Owner clarification (2026-11-22): no ModMed API yet; manual report exports are the feed, an API may replace them later

ModMed has not approved an API connection. The A/R and Production reports exported by hand from EMA (dropped in Dropbox or
uploaded) ARE the EMA feed, and the build must not depend on any ModMed approval. If ModMed later offers an API, it may or may
not carry payment data; if it does, it must plug in without touching the checks, outcomes, story, verdict or next step.
- Source-independent seam (wiring phase; small): `ema_ledger.apply_postings(conn_or_handle, source_id, box, postings)`
  applying an iterable of `ema_reports.Posting` for one box (posted-date or service-date range, as today) with the same
  replace, hold and invariant rules as a file; `ema_reports.apply_ar_snapshot(conn, source_id, as_of, rows)` for A/R rows.
  The file readers become one adapter that calls these. Record the source kind (file, upload, api) on each applied batch.
  No API adapter is built now.
- The Upload page guide and docs/OPERATIONS.md describe the weekly routine as the supported feed (named person and a
  backup, Monday, five exports, the freshness alerts), and say what to ask ModMed for if an API is offered: line-level
  payment and adjustment postings with check or EFT number, posted date and reason code; A/R balances by line; claim status
  with dates; charges with claim id and modifiers. And to ask whether EMA's reports can be scheduled to a secure destination
  (SFTP or a shared folder), not email.
- Freshness alerts stay the guard: A/R older than 8 days, Production older than 14 (EMA_STALE), shown on Today and in email.

## 11. Owner requirements (2026-11-22): 200-300 MB uploads in the app, deduplication of repeated exports, archive old data off the daily run

11.1 Large report uploads, both sites. The Upload page sends any file over 20 MB in 4 MB chunks (under Vercel's ~4.5 MB request
limit): POST /api/uploads/chunk {upload_id, index, total, chunk_sha256}; GET /api/uploads/{id}/status lists received chunks so a
broken upload resumes; a final call carries the whole-file sha256. The browser gzips text reports on the fly with
CompressionStream('gzip') (a 300 MB CSV becomes about 25 MB; fall back to plain chunks where unsupported); the server accepts
both. Server mode: chunks append to /data/incoming/<upload_id>.part (mode 0600), are verified, renamed on completion and handed
to ema_reports.ingest_file, which streams .csv.gz and .zip (never the whole file in memory). Serverless: each chunk is relayed
to a Dropbox upload session and committed into an intake folder the next run reads; a gzipped Production file under 60 MB is
then readable on Vercel inside one run. Limits: 2 GB compressed per file (configurable). Progress bar, pause and resume, and a
plain result: what the file was recognised as, its scope (service year or posted-date range), rows, boxes replaced, holds.
The 60 MB cap stays for the old weekly exports that are parsed in memory. Owner-gated? No: any signed-in biller may upload.

11.2 Deduplication guarantees, tested on the real files: the same file twice (or re-saved with identical content) changes
nothing; the whole 2026 export again a month later replaces the 2026 box and no posting is counted twice; weekly posted-date
pulls after a year file, and a year file after weekly pulls, end with the same totals as one fresh full export; an upload and
the same file in Dropbox apply once; each A/R snapshot is dated, the newest is current, earlier ones are kept as history; the
same 835 file again is ignored (as today). The weekly routine recommends Production by posted date, last five weeks (~20 MB);
year files only for catch-ups.

11.3 Retention and archive (future-facing memory). Hot data is what a daily run needs: open items, their claims and lines, and
the history the rules need (payer rate baselines over their measured window). Everything else goes to compressed archive files
that the daily run never loads (server: /data/archive; serverless: the state folder's archive/), read on demand by Find a claim
and the claim view: lines and postings with service older than the retention horizon and no open item, ledger boxes older than
1,100 days, A/R snapshots older than 13 months. Items past every appeal and filing window, plus 30 days, are retired from the
worklist with their proof (still findable, reopenable). Each run records hot-store size and peak memory against a budget
(serverless budget 1.6 GB of the 2 GB limit; today's peak 1,530 MB of 2,278 MB); Data health warns at 80% and names the largest
tables. Owner decision: the retention horizon (default 36 months of service) and keep vs delete archives (default keep; EMA
and TriZetto remain the records of truth).
Acceptance: the real archive with the horizon applied: hot-store size, Vercel peak memory and run time before and after;
every retired item still opens from Find a claim; the worklist's open-item count and dollars by signal before and after,
with every removed item accounted for (expired vs archived).

## 12. Owner requirement (2026-11-22): the ModMed upload section labels exactly which exports are needed and how to get them

The Upload page gets an "EMA exports" checklist, one row per export, replacing the three-report list:
1. A/R report (all 22 columns; every aging bucket or Total A/R if EMA offers it; no filters) weekly.
2. Production (collections) report (all 39 columns; filter Posted Date, last five weeks; no other filter; CSV) weekly; the
   whole service year only to catch up.
3. Encounter & Charge Lines (full layout), 4. ERA & Payment Line Adjustments, 5. Claim & Claim Status (with Patient ID):
   weekly, pulled by posting or transaction date over the last five weeks where EMA allows (else by service date, six weeks).
Each row: the report's name as EMA titles it (matching the sample files), one line on what it is for, the filters, the
required columns (checked on upload), format (CSV or Excel; any size: the page compresses and chunks it, section 11), cadence,
when it last arrived and whether it is overdue (A/R 8 days, Production 14, weekly exports 8), and an editable "How we pull this
in our EMA" box (stored in the store's meta, signed-in users may edit, history kept) that holds the practice's own click path.
Never invent EMA menu paths: until the box is filled, the row says to ask the person who last pulled it. If the owner sends
screenshots of the EMA menu and filter screens, the steps are written from them.
Upload-time checks with plain messages: wrong report; missing columns; looks filtered (fewer payers, providers or locations
than the last good file); dates that do not cover the expected range; the 30-day-only A/R (aging_window 30) with the request
to EMA for every bucket; a re-upload of a file already applied ("already have this; nothing changed").

## 13. The owner's two goals are the acceptance gates (2026-11-22)

"What I don't want is any missed opportunities (claims that are losing dollars but we have a chance to recoup) and any claims
that look like they need to be worked but have actually already been worked."

13.1 States. Every item is in exactly one of: WORK NOW (needs a person today or this week); WAITING (a person or EMA shows the
work was done: appeal or corrected claim faxed or sent, a resubmission on a later 835 or claim-status row, an EMA claim action,
an A/R note about an appeal, call or resubmission; follow-up date = the date the work was seen + the payer's response time,
p90 from the archive; returns to WORK NOW only if the payer has not answered by then); DONE (paid, settled by patient or
secondary, with proof); ACCEPTED (owner's policy pattern); EXPIRED (past every window + 30 days; archived per section 11.3);
CAN'T BE CHECKED (the money never reaches any source, e.g. a surgery-center remittance or a missing 835: listed as a data gap,
never counted as fine). Today and the default Worklist show WORK NOW and due follow-ups only. EMA's A/R notes are undated: a
note is dated by the first weekly A/R pull it appears in (pull dates are kept); the very first pull dates every existing note
to that pull, and says so.

13.2 Gate 1, missed money (recall). An independent builder (not the code under test) lists every 2025+ line still losing
recoverable dollars from all sources: a denial or open insurance balance inside an appeal, reconsideration or corrected-claim
window; an unanswered claim inside its filing limit; a short payment against the payer's own rate (contract rates where loaded);
an 835 payment EMA never posted; a secondary never billed; an auto write-off still appealable and not ACCEPTED. Compare with
the app's WORK NOW + WAITING items: every line on the independent list and not in the app is a miss; fix it or explain it line by
line with dollars. Target: zero unexplained misses.
13.3 Gate 2, wasted work (precision). For every WORK NOW item, an independent check that it truly needs work: not paid (before
or after the denial, any check, with or without an 835), not settled by the patient or the secondary, not already worked and
waiting, not a duplicate listing, not a surgery-center twin, not expired, not ACCEPTED. Every WORK NOW item that fails is wasted
work; fix it or explain it. Target: zero unexplained.
13.4 Both gates run on the full 2025+ population in the end-to-end verification (both modes), report the confusion matrix
(needs work and listed; needs work and missed; no work and listed; no work and not listed) by count and dollars, and list every
off-diagonal line (de-identified in the report; identified only in the scratchpad). The 67-case practice-manager walkthrough is
the deep proof (full evidence per case); the population gates are the breadth.

## 14. Rules the practice-manager trace found (batch C01-C11; skeptic-pending; the wiring must implement or disprove each)
- Bulk "Appeal everything appealable" must never include an item EMA shows paid or settled, a credit, a line reprocessed in the
  archive, or a WAITING item. (C03: would have appealed a paid line; C05: a rate appeal would have grown an overpayment.)
- Close from EMA payments on checks with no 835 (C6) with the proof row; when EMA moved the line to another payer and that payer
  paid, close with "rebilled to <payer>, paid" (C02).
- CO-B11 (or a claim status 19 with a crossover carrier named on the 835) = "forwarded to <carrier>: expect payment", a chase
  timer (45 days), not non-covered (C08).
- "Paid before" must cover any denial on a line already paid by the right payer (not only duplicates), including a payer that is
  not the patient's plan denying after the primary and Medigap paid (C09).
- A denied frequency-7 replacement while the original's payment on the same line control number stands unreversed = "paid;
  replacement denied; watch for a takeback" (C10).
- A status-22 reversal followed by re-adjudication to PR on the same claim = moved to the patient (C11).
- UNDERPAID_VS_OWN_RATE needs the later-payment and reprocess check (C05); an overpayment (credit) gets a refund workflow
  (credit item: who overpaid, how much, refund or recoup, owner/compliance decision).
- A PLB WO takeback on a line EMA never booked = "post the takeback in EMA" item (C03).
- Missing-ERA list (C1) with one-click re-send request text per check (payer, check date, amount) (C04, C07).
