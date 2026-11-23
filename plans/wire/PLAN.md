# Push 2 plan: wire EMA's A/R and Production reports into RemitWatch, on both hosts (scratchpad only, never commit)

S = /tmp/claude-0/-home-user-Billing-Optimizer/96e81489-2ad2-5c70-991d-e00db2e22749/scratchpad
Repo: /home/user/Billing-Optimizer (branch claude/ecstatic-fermi-orfcgo). main deploys BOTH sites: Vercel (serverless; store image
and state in Dropbox /RemitWatch-state; 2 GB memory, maxDuration 300 s, /tmp 512 MB; daily cron 10:17 UTC) and AWS
(server mode, Docker; store on disk under /data; Dropbox webhook + 5-minute auto-deploy of main). Both run the same code.

## The owner's words (the acceptance standard)
- "What I don't want is any missed opportunities (claims that are losing dollars but we have a chance to recoup) and any claims
  that look like they need to be worked but have actually already been worked. That's wasted time and effort."
- ModMed has not approved an API. The A/R and Production (collections) reports exported by hand from EMA ARE the feed (dropped in
  Dropbox or uploaded in the app). An API may come later, maybe without payment data.
- Collections reports are 200-300 MB: they must upload in the app; repeated exports (all of 2026 again next month) must be
  de-duplicated; stale data past any useful window must be archived so it does not eat memory on every run. Future-facing.
- The upload section must say exactly which exports are needed and how to get them from ModMed.
- Flag EMA's automatic write-offs: "looks resolved in EMA, nobody worked it".
- Every item shows its story (evidence trace), its verdict and its exact next step, one click where possible (fax, pulled note).
- Verify end to end CORRECTLY with the manual workflows (Dropbox and manual uploads) before pushing to main.

- (2026-11-22, later) "By the end of this, it needs to run independently ... self regulating, and self-checking, and maintain
  itself ... re-evaluating its performance regularly ... finding and understanding rule based patterns so that things don't get
  missed, and AI is only used for the extreme edge cases." So: every unit's work must run on its own inside the daily cycle on
  both hosts (no step that needs a person except business decisions: the policy table, letting an item go, sending a fax);
  detection, outcomes, states, closures and audits are rules only; AI stays only where it is today (note review for appeals,
  reading incoming faxes, a template on request), always waiting for a person. Unit G (${S}/wire/brief_G.md) builds the
  self-regulation, the per-run invariants with safe mode, the weekly self-audit, the track record and the pattern watch.

- (2026-11-22, later still) "At some point, people will stop double checking your work ... The software will be the source of
  truth. If the software is silently losing money, then it is useless. We are in a margins game, and we are losing." So these are
  NON-NEGOTIABLE for every unit: (1) every dollar is accounted for: a line's and the practice's money reconciles to the cent each
  run (charge = insurer paid + patient paid + contractual + written off + open +/- credits) and any unreconciled dollar becomes a
  visible gap with an amount, never a silent drop; (2) every write-off has an owner: money written off with no person's decision
  and no owner-approved policy is counted every week by payer and reason, with what is still recoverable; (3) blindness is loud:
  when a feed (835s, EMA reports) is stale, every page says in dollars what the app cannot check; (4) work is ranked by money:
  each item carries its dollars x the payer's historical recovery rate for its pattern (payer group x code x CARC, from the
  archive, with the basis shown) and the worklist sorts by that and the deadline; (5) a scoreboard: recovered, lost to
  deadlines, at risk in the next 14 days, given up without a decision, week by week. A unit that cannot meet one of these says so
  in its report with the reason.

- (2026-11-22) "Extensive logs that can be audited ... so detailed that it should be able to recursively fix its own issues, or
  produce a log so detailed that an intern using Claude can diagnose and push the fix." Unit H (${S}/wire/brief_H.md): run
  manifests, a structured event log, a decision log on every automatic change to an item ("Why did this change?"), a rule-based
  self-repair registry, a de-identified diagnostic bundle, CLAUDE.md and docs/TROUBLESHOOTING.md. Every unit: no silent excepts;
  every automatic decision records its rule, version and evidence.

- (2026-11-22) "Our moat will be the tracing logic of how to work claims reliably, and how to work through these messy pools of
  data. ... If people hand test and realize a claim you thought was unworked was worked and resolved, or a claim you stated was
  resolved was in fact not, and left money on the table, RemitWatch will never be used again. ... a dashboard that keeps track of
  the numbers. The money that RemitWatch is actually bringing back ... ROI ... full transparency into the work being done." So:
  every verdict carries its proof (dated evidence reconciled to the cent, the rule that decided it); a person can hand-check any
  item in under two minutes; unit R (${S}/wire/brief_R.md) builds the Results and ROI tab with honest attribution (after
  RemitWatch work / found by RemitWatch / arrived on its own), lift against similar unworked items, work done, outcomes, hours
  saved, denials reduced, cost and ROI, and daily spot checks whose agreement rate is measured and whose disagreements become
  diagnostic cases.

- (2026-11-22, evening) "Find and fix every single possible bug along the way, and continue to add logging ... build out your test
  suite to test every single edge case you can think of ... someone accessing either Vercel or AWS site can see an awesome product.
  ... keep the core principle of making it very user friendly for our biller/practice manager ... so easy that someone even without
  experience should be able to pick it up and someone with experience wouldn't be annoyed by over guidance." So: every bug found
  gets a failing test first; every unit adds edge-case tests (empty, duplicate, malformed, huge, unicode, leap-day and year-end
  dates, reversals, negative and zero amounts, two people acting at once, both hosts) and randomized tests on invented data for
  readers and money invariants; pages follow the ease-of-use rules in brief_C.md.

## Inputs every unit reads
- Spec: ${S}/arcoll/design_final_spec.md (sections 0-15) and ${S}/build/amendments.md (sections 0-14; overrides the spec).
  Read amendments sections 0, 4, 5, 8, 9, 10, 11, 12, 13, 14 in full before coding.
- What the three modules expect from the wiring: ${S}/build/needs_ema_reports.md, needs_ema_ledger.md, needs_ema_checks.md.
- Real-file layouts (no PHI): ${S}/arcoll/layout.md.
- Practice-manager walkthrough (66 verified cases, de-identified): ${S}/pm/worksheet.json; identified keys only in
  ${S}/pm/sample.sqlite and ${S}/pm/trace_*.sqlite (PHI). Read the worksheet's product_gap fields: each is a rule to build or disprove.
- The modules: remitwatch/ema_reports.py, ema_ledger.py, ema_checks.py (built and reviewed; inert until this wiring).
  Three reviews ran on them (correctness, safety, scale on the real files in both modes): their notes and scripts are under
  ${S}/build/review-correctness, review-safety, review-scale; the fixer applied the findings (its report is in
  ${S}/build/fix_review_report.md when present). Read the scale review's numbers before touching the serverless path: apply
  runs peak 1,229-1,328 MB, a detection-like run 1,365 MB, a full cycle with EMA tables 1,460 MB (limit 2,278); /tmp is 512 MB
  and the ledger outgrew it inside its retention window (high finding); dashboard routes must not recompute EMA figures from
  the dashboard copy on Vercel (high finding): they read what the run stored in meta.
- Design note: never de-duplicate individual postings by content (identical postings in the files are real: 487 of them).
  De-duplicate FILES (byte sha and a content hash of the parsed rows) and BOXES (newest export of a box wins).

## Real data (PHI; read in place, never copy the big CSVs, never print rows)
- ${S}/arcoll/raw/ar2025.xlsx, ar2026.xlsx (A/R), prod2025.csv (206 MB), prod2026.csv (160 MB) (Production). Same bytes as the
  four files now in Dropbox at "/Trizetto Data/Trizetto/AR and Collections Reports from EMA/" (names: "AR 2025.xlsx",
  "AR 2026.xlsx", "2025 production comprehensive(Sheet1).csv", "2026 Production(Sheet1).csv").
- ${S}/eg2/store.sqlite: server store after a full cycle on the 835 archive (open with sqlite URI mode=ro; copy before writing).
- ${S}/live_state_1122/store.sqlite (+ cursor.json): Vercel's actual store image from today's 10:17 UTC cron, with the billers'
  real statuses, notes and faxes. The end-to-end runs start from this.
- ${S}/data/dropbox/: a local mirror of the Dropbox tree as of 2026-11-06 (1,420 files: 835s, weekly EMA exports). For Dropbox-route
  tests build a mirror in YOUR work dir (hard links or symlinks to data/dropbox plus links to the four report files at the path
  above). Never modify data/dropbox.
- Shared Dropbox mirror, ready to use: ${S}/wire/mirror/ ("Trizetto Data/..." as in Dropbox, 1,423 files: the 835s and weekly
  exports as of 2026-11-06, which is everything Dropbox has had since the feed stopped, plus the four report files at their
  Dropbox path). Every file is a HARD LINK to the originals (data/dropbox, arcoll/raw): open read-only; never write, truncate,
  rename or delete inside it; copy a file out before changing it. Root for the sync code: "/Trizetto Data".
- Live Vercel baseline (status.json of the 2026-11-22 10:17 UTC run): 0 files listed, 16,165 findings, detect 51.7 s, peak memory
  1,530 MB of 2,278 MB, whole run 98.6 s of a 240 s budget, newest 835 payment 2026-10-09 (STALE_FEED alert active).
- World builder: python3 -I -B ${S}/build/ema_checks/accept/b0_build.py <workdir> (copies eg2 store, ingests the four reports,
  builds the ledger; ~40 s; ~1 GB). One world per unit, in your work dir; delete it when you finish.

## Rules for every agent
- PHI: identified material (names, MRN, DOB, member ids, claim ids, check numbers, claim links, exact dates of one claim) stays in
  sqlite files under your work dir in ${S}. Everything printed, returned or written as text is aggregate or uses RemitWatch finding
  ids, payer, codes and dollar amounts. Run scripts that read real files with python3 -I.
- Public repo: no personal names, no EMA tenant host name, no Site ID, Tax ID, NPI, claim ids, check numbers or practice-specific
  payer aliases in code, tests or docs. Tests use invented rows (tests/ema_fixtures.py).
- Do not git commit, push or reset. Edit only the files your unit owns (below). Anything you need from another unit goes into
  ${S}/wire/needs_<your unit>.md with the file, the function and the exact change. Other units edit other files at the same time:
  if a test fails in a file you do not own, check `git diff` on it before acting; a half-finished edit by another unit is not yours
  to fix; re-run later.
- Both hosts: every feature works in server mode (store on disk + ledger file) and serverless mode (compact in-memory store
  image, state in Dropbox, ledger opened only on apply runs, 2 GB, /tmp 512 MB, 300 s). Test both.
- Tests run in three environments, all must pass: `python3 -m pytest -q` (3.13, SQLite 3.53),
  `${S}/fix/venv2_compat/bin/python -m pytest -q` (3.13, newest libraries), `${S}/fix/venv2_compat312/bin/python -m pytest -q`
  (3.12.3, SQLite 3.45.1: what AWS and CI run; SQL must not name an outer table inside a subquery's ORDER BY), plus
  `node tests/calc_model_check.js` and `node tests/lab_review_check.js` when you touch the pages.
  Fourth environment, for Vercel's SQLite: `${S}/fix/venv_sqlite340/bin/python -m pytest -q` (Python 3.11.1 with SQLite 3.40.0,
  the version the live Vercel runtime reports: Python 3.12.16 + SQLite 3.40.0). This build has no Connection.serialize, so only
  tests that serialize a store image may fail there (AttributeError 'serialize'); every other failure is real. Vercel has
  serialize. No SQL function newer than 3.40 (unhex, octet_length, timediff, concat, concat_ws, string_agg, ORDER BY inside an
  aggregate, jsonb_*, IF() as a function).
- Break and restore: for every new test, break the code it guards on purpose, see the test fail, restore, and confirm with
  `git diff` that the restore is exact.
- Budgets: disk is tight (about 6-9 GB free for everyone). One world per unit. Peak memory per agent under 3 GB; serverless
  simulations must stay under the 2 GB limit (measure with resource.getrusage or /proc/self/status).
- Wording on pages: plain English, short sentences, no em dashes, no jargon such as "false positive". Say what happened and what
  to do. Dates in the app are fine (it is behind the login); the names switch governs patient details.

## Shared names (decided here so the units agree)
- New finding columns (unit B adds them to detect.EXTRA_FINDING_COLS and api.FINDING_COLS; C and D read them):
  written_off_source, written_off_kind, written_off_when, ema_link, ema_status, ema_status_src, ema_as_of, ema_balance_cents,
  ema_worked_on, ema_worked_how, source, ema_outcome, ema_outcome_on, ema_writeoff_evidence ('rule_proven' | 'posting_day'),
  work_state ('work_now' | 'waiting' | 'done' | 'accepted' | 'expired' | 'cant_check'), follow_up_on (YYYY-MM-DD),
  state_basis (short text naming the decisive fact).
- remitwatch/story.py (unit D): item_story(conn, finding_id=None, *, claim_key=None, ledger=None) -> {'rows': [...],
  'balance': {...}, 'reconciles': bool, 'gap_cents': int}; item_verdict(conn, finding_id, *, ledger=None, today=None) ->
  {'state', 'sentence', 'rows', 'strength' ('proven' | 'one_source' | 'inferred'), 'rule'}; next_step(conn, finding_id, *,
  ledger=None, today=None) -> {'action', 'who', 'deadline', 'basis', 'expected_cents', 'buttons', 'instructions'}.
  Story row: {'n', 'date', 'source' ('835' | 'production' | 'ar' | 'weekly' | 'claim_status' | 'remitwatch'), 'kind', 'payer',
  'amount_cents', 'codes': [{'code', 'meaning'}], 'text', 'decisive': bool}.
  Button ids: build_packet, attach_note, find_note, upload_note, fax_payer, copy_instructions, mark_sent, log_outcome,
  mark_posted, mark_billed, accept_pattern, let_go, reopen, request_era, open_in_ema.
- THE UPLOAD HAND-OFF (A2 writes, A1 reads). A finished upload becomes one file plus a JSON sidecar in an intake folder:
  server: <data_dir>/incoming/ready/<upload_id>__<safe name>[.gz] (mode 0600); serverless: Dropbox
  <REMITWATCH_STATE_PATH>/intake/<upload_id>__<safe name>[.gz]. Sidecar <same name>.json = {"name": original file name,
  "origin": "upload", "user": signed-in user, "sha256": sha256 of the ORIGINAL (uncompressed) bytes, "size": original bytes,
  "gz": true|false, "received_at": ISO time, "report_hint": what the browser recognised from the header row or null}.
  The original-bytes sha256 is what makes an upload and the same file in Dropbox apply once. A1 owns remitwatch/intake.py:
  process_ready(store, ...) on a server (called by the pipeline at the start of each cycle and by intake.kick(data_dir), which
  A2 calls after a finish to start processing soon, under the pipeline's lock), and the serverless run reads the Dropbox intake
  folder before the Dropbox delta (streaming .gz, verifying the sidecar sha256 while reading, ingesting with origin 'upload',
  moving the pair to intake/done/ after it is applied or refused, with the result recorded where the Upload page can show it).
- remitwatch/states.py (unit D): the ONE place an item's state is decided. state_of(conn, item, *, outcome=None, today=None)
  -> {'work_state', 'follow_up_on', 'state_basis', 'evidence': [...]}, with work_evidence(conn, item) gathering dated work
  (RemitWatch statuses, events, faxes and notes; EMA Claim Action; A/R Last Billing Note about an appeal, call or
  resubmission, dated by the first A/R pull it appears in; a later 835 or claim-status row showing a resubmission) and
  response_days(conn, payer_group) (the payer's p90 answer time from the archive). Unit B calls state_of at save time and
  stores work_state, follow_up_on and state_basis; story.item_verdict explains the stored state with its decisive rows.
- remitwatch/api_uploads.py (unit A2): an APIRouter with POST /api/uploads/chunk, GET /api/uploads/{id}/status,
  POST /api/uploads/{id}/finish, GET /api/ema/exports (the five-export checklist with last arrival and overdue flags),
  PUT /api/ema/exports/{key}/howto (the editable "How we pull this in our EMA" box, kept in meta with history).
  api.py gets one line `app.include_router(api_uploads.router)`, added by A2 (unit C is told).

## Units and file ownership
Phase 1 (two workflows run side by side):
- A1 intake-core: remitwatch/ema_exports.py, dropbox_sync.py, cloud.py, pipeline.py, store.py, state.py, cli.py, new intake.py,
  ema_reports.py and ema_ledger.py (wiring edits), the one-line er.ensure_schema call in detect.ensure_schema, .env.example (EMA
  variables), vercel.json/api/index.py only if needed; tests: test_cloud.py, test_sync.py, test_parity.py, test_ema_reports.py,
  test_ema_ledger.py, tests/ema_fixtures.py, new tests/test_ema_intake.py.
  Builds: Step 0 (guard, recognition first, migrate_misread once per host, keep a genuine Claim & Claim Status export),
  re-find report files the Dropbox cursor already passed (first wired run scans the root for recognised reports), Step 3
  (server ledger per cycle, refresh before detection, backups), Step 4 (serverless: LedgerHandle on apply runs only, note_attempt
  before the read, save order, one Production file per run with more=True, the repair path, files over 60 MB wait with a plain
  message), the section-10 seam (apply_postings, apply_ar_snapshot, source kind on each batch), the section-11.2 dedup guarantees
  (file content hash so a re-saved identical export is 'already'; whole-year re-export replaces its box; posted-date pulls and year
  files in either order give the same totals; upload + same file in Dropbox apply once; A/R snapshots dated and kept), the
  serverless intake folder for uploads (contract above), deterministic xlsx fixtures (openpyxl save time and zip timestamps made
  the A/R tests flaky), the VA alias (codes rule above OPTUM, VACAA) if not already done.
- A2 uploads: remitwatch/uploads.py, new api_uploads.py, static/upload.html, new static/upload.js, the include line in api.py;
  tests: test_uploads.py, new tests/test_upload_chunks.py; a Playwright script under ${S}/wire/a2/.
  Builds: section 11.1 (4 MB chunks, resumable, in-browser gzip with CompressionStream and a fallback, whole-file sha256, server
  appends to /data/incoming/<id>.part mode 0600 then hands to intake.accept_upload, serverless relays each chunk to a Dropbox
  upload session into the intake folder, 2 GB compressed cap, progress, pause, resume, cleanup of abandoned parts, any signed-in
  user), section 12 (the five-export checklist: name as EMA titles it, what it is for, filters, required columns, format, cadence,
  last arrival and overdue, the "How we pull this in our EMA" box; never invent EMA menu paths; upload-time checks: wrong report,
  missing columns, looks filtered, dates not covering the range, 30-day-only A/R with the request to EMA, already applied).
- B detect: remitwatch/detect.py (not ensure_schema's one line), actions.py, insights.py, alerts.py, codes.py, report.py,
  ema_checks.py (wiring edits and fixes found on real data); tests: test_remitwatch.py, test_features.py, test_insights.py,
  test_resolution.py, test_workable.py, test_review_*.py, test_ema_checks.py, new tests/test_ema_wiring.py.
  Builds: needs_ema_checks.md sections 1-3 in the order of amendment 4.5, the outcomes of amendment 8 (auto_written_off with its
  evidence level, written_off_while_pending, lost_to_timely_filing, precedence, the policy table), the states of 13.1
  (work_state, follow_up_on, state_basis), the rules of section 14 and every product_gap in pm/worksheet.json that is a rule.
- B2 archive-only rules (after B, same files): amendment section 7 (paid before by the same payer reclassified, another payer paid
  first, units, the later-payment check for short payments, same-day remittances, second payer under half, folding repeat denials
  of one service, PR-group denials that are not deductible/coinsurance/copay and CO-147, hide rules at payer x code x modifier x
  reason grain, expected amounts for drugs and skin substitutes, EXPIRED for items past every window + 30 days (archive is unit E).
- D story: new remitwatch/story.py and remitwatch/states.py, new tests/test_story.py and tests/test_states.py (section 9 contract above; every signal and every outcome; the
  balance line reconciles to the cent or shows the gap; works with and without a ledger).
Phase 2: C api+pages: remitwatch/api.py, static/index.html, desk.js, features.js, today.js, faxes.js and other pages; tests:
  test_api_review.py, test_desk.py, test_claims.py, test_review7.py, new tests/test_ema_pages.py.
Phase 3: E retention/archive (new remitwatch/archive.py + small hooks); then G self-regulation and self-audit (brief_G.md) and
  H audit logs, self-repair and diagnostics (brief_H.md) side by side; then F docs (README, ARCHITECTURE, docs/*; after G and H).
Phase 4: verification (end to end on real data in both modes, the two gates of 13.2-13.4, the 66-case click-through with
  screenshots, names off), fix loop, independent review, final suite, push.

## Revised schedule (2026-11-22 23:15) and release gates
Phase 1 finishing: A1 (build, reviews, fix), A2 (reviews, fix), B (reviews, fix), B2 module (measure), D (build, reviews, fix),
answer keys (reconcile, freeze into ${S}/truth/HOW_TO_RUN.md + confusion.py).
- INT logic integration (${S}/wire/brief_INT.md): after B's fix, B2's module and D's fix. Wires the archive rules, the states of
  their reclassifications, the ledger's loose reversal pairing, OA-100, the PM mismatches, EMA's split payer group names, the A/R
  note kinds (after A1's fix), and measures both gates on the integrated real world; the serverless memory on the real image.
- Phase 2 (C1, C2, R) starts as soon as the bulk-appeal hotfix is on main and merged into the dev branch (C1 builds on it).
- Phase 3a (E, G, H) starts now as MODULES ONLY (new files, tests, needs files with exact hook bodies, like B2 did): they must
  not edit pipeline.py, cloud.py, detect.py, api.py or pages. Phase 3b: one hook-in agent wires E, G and H into both cycles, adds
  remitwatch/api_ops.py (their routes) and static/ops.js (the Data tab panels: "Running on its own", "Logs and diagnostics",
  "Store size and memory"), after A1's fix, INT and C2 are done. Then F (docs). Then phase 4 verification and the fix loop.
- The bulk-appeal hotfix (on main): REMITWATCH_BULK_APPEAL, default off; batch sends carry bulk:true and are held with code
  bulk_paused; /api/appeals/appealable returns {paused: text, items: []} while off. Push 2 keeps the switch; the default turns on
  only if verification shows that no item in the bulk list is paid, settled, credited, reprocessed or waiting (gate 2 oracle).

Release gates for push 2 (all must hold, measured by phase 4, not taken from a unit's report):
1. Full suite green in the four environments (serialize-only failures on venv_sqlite340) + both node checks, on a clean checkout.
2. Gate 1 (missed money) and gate 2 (wasted work) against the frozen answer keys, numbers in the release note.
3. Serverless peak memory under 1.75 GB and run time under 180 s on Vercel's real image (live_state_1122) with the four reports
   ingested over successive runs; /tmp under 512 MB. Unit B saw 2.4 GB in an emulation: this gate is not optional.
4. Both modes give identical items; a second cycle changes nothing (idempotent); the billers' statuses, notes, faxes and hide
   rules from the live image all survive.
5. Money conservation to the cent on the integrated world; every automatic closure has its proof rows; nothing closed by EMA
   status alone.
6. The 66 practice-manager cases: verdict and next step agree (64+), opened by deep link in the real app (names off).
7. No PHI in the repo diff, logs, bundle or screenshots (scan); no secrets; no practice host names.

CPU rule (4 CPUs shared by many agents): run your own and related tests while working; the full suite ONCE at the end in two
environments (venv2_compat312 and venv_sqlite340). The orchestrator runs all four before release.

## Needs sweep (2026-11-22 23:45): nothing asked for in a needs file is dropped
The phase-1 fix stages read only their own reviews, not other units' needs files. So the 3b hook-in agent also reads EVERY
${S}/wire/needs_*.md and applies each item aimed at A1's files (intake.py, pipeline.py, cloud.py, dropbox_sync.py, store.py,
state.py, cli.py, ema_exports.py) that is not already done (check the code first), with a failing test first; INT does the same
for detect.py, states.py, story.py, archive_rules.py, insights.py, codes.py, ema_checks.py (and ema_reports.py/ema_ledger.py after
A1's fix). Known cross-unit items: the A2 security review's gzip bomb / giant line (a 1 MB upload that stops every cycle: needs a
cap on decompressed bytes and on line length in intake.py and ema_reports.py's readers, with the file held and named, never a
stopped cycle); D's A/R note kinds (INT item 7). Phase 4 lists every needs item with its status (done, not needed, owner decision).

## Orchestrator decisions (2026-11-22 23:40; every unit follows these)
1. EXPIRED starts the day the last window closes (the payer's own limit when it is later than the safe date). No 30-day grace in
   the work queue: an item past every window is not work. The 30 days apply only to unit E's archive retirement.
2. Bulk appeal ("Appeal everything appealable", ticked items) uses the SAFE date only (appeal_deadline >= today) AND B's SQL AND
   next_step appeal_ok. An item past its safe date but inside the payer's own limit is appealed one at a time from its panel,
   where the step says "the safe date passed, but the payer's own limit is <date>: send it now".
3. The owner's write-off policy key for an EMA_DENIAL_AS_CONTRACTUAL item is 'payer group|||reason' (the pattern with no code and
   no modifier); ema_checks.set_policy and the policy page use exactly that key.
4. An archive-rule 'likely' done never closes an item; its next step is a "Check first" route naming the one thing to verify and
   where (EMA, the payer's portal), then Close as paid or Appeal.
5. Every dollar figure shown carries its basis on hover (835, EMA posting, A/R, estimate); estimates are labelled.
6. (Release decision, 23:55) REMITWATCH_EMA_REPORTS defaults to 'all' on a server and 'ar' on Vercel "until Production passes the
   release gate there". Phase 4 runs Vercel WITH Production ('all') and measures gate 3 on it; if it passes, the default becomes
   'all' on both hosts in the release commit (one line + its test), so both sites read the same reports with no setting to change.
   A Production report kept waiting while a host did not read it must be read once the host reads Production (A2 e2e high finding;
   cloud.py: unit SWEEP).
7. (00:45) The diagnostic bundle download is open to any signed-in user (a biller is told to download it by the alerts; it holds
   no patient data: the scrubber and the second pass prove it), logged with who and when, no-store.
8. (00:45) Log retention (server 180 days / 512 MB, Vercel 90 days / 100 MB) and decision retention (400 days, 500,000 rows) stay
   constants named in one place in runlog.py and documented in docs/TROUBLESHOOTING.md; no new setting.
9. (01:15, THE OWNER: "With AI, every dollar amount is worth chasing. No dollar amount is too small.") No dollar floor anywhere
   in what RemitWatch detects, lists, states, ranks or alerts on. Small items are ranked lower (expected value), never hidden or
   dropped, and flow through the automated paths (bulk appeal when it is on; batched per payer where one letter can list many
   claims, so a $2 item does not cost a $0.40 fax of its own). What stays filtered is NOT money, named by what it is, never by size:
   rounding differences of a cent between EMA and an 835, the token pieces of a re-adjudication (B13 lines), informational
   remittances that moved nothing. Known floors to remove (with a failing test first, then measured on the real world):
   - detect.py downcode check: `(l.get("charge_cents") or 0) < 2000` skips every line charged under $20 (keep only the B13 /
     token-piece test, by what the line is);
   - detect.py fee-schedule insight: charge >= $5 and gap >= $100 (statistical minimums of 5 lines may stay; dollar floors go);
   - ema_checks.py UNPOSTED_MIN_CENTS = 100 (unposted 835 money of $1 or less is not listed): only exact rounding (a cent) stays out;
   - insights.py SMALL_CENTS + today.js "N of the items are worth less than $20 each ... the Worklist's amount filter hides them"
     (a nudge to ignore small items: remove the nudge; the Worklist's optional amount filter may stay, default Any amount, worded
     "$20 and up" not "Hide under $20");
   - desk.js ALERT_FLOOR = 50000 (payer changes under $500 get no line on a category page): show all, smallest folded under
     "N smaller changes";
   - selfcheck.py AUDIT_GAP_ALERT_CENTS and GIVEN_UP_ALERT_CENTS = $500: any new coverage gap and any rise in money given up raises
     the alert (one alert per run listing them all);
   - "not worth pursuing" as a suggested let-go reason (today.js) and any step that tells a person to let an item go for its size;
   - the answer keys' under-$5 floor (truth/g1_final, g2_final, confusion.py): re-keyed without it.
   The owner's write-off policy question "a small-balance threshold" is answered: none. EMA writing off a small insurance balance is
   an automatic write-off like any other (still appealable while its window is open).
10. (01:30, THE OWNER: "If it's easy enough to click a few buttons and takes under a minute, even $5 is worth chasing. In fact,
   even $1 per minute is more than our billers get paid, so that's positive ROI.") The yardstick is dollars per biller-minute.
   - story.next_step returns `minutes` (the route's estimated biller minutes) and `per_minute_cents` (expected value / minutes);
     detection's save writes `minutes_est` and `value_per_minute_cents` on findings beside expected_value_cents.
   - Minutes per route come from ONE editable table (the Results tab's assumptions, unit R's store: the same minutes that size
     "hours saved"); defaults: one-click fax appeal from the panel 1, a batch fax per payer 1 per payer, corrected claim 5, portal
     check 5, post a payment 3, bill the secondary 3, bill the patient 2, ERA request 2, chart review and appeal letter 10, payer
     call 15, refund decision 10. The owner edits them; history keeps which values were in force.
   - The worklist and Today sort by value_per_minute_cents (then the deadline); the dollar sort stays one click away.
   - $1 a minute (owner-editable) is the line: items above it are positive ROI for a person; items below it are still listed and
     still chased, marked "batch or automate" (bulk appeal, one letter per payer), never hidden or let go for their size.
   Owners: INT (story.next_step minutes and per-minute, the save columns), HOOK (G's expected values times the minutes; api.py and
   page sort; R's assumptions table as the single source of minutes), V (checks the sort and the $1 line on the real data).
11. (01:45, THE OWNER: "reduce the time it takes to chase everything by so much that even 0.60 is worth chasing. We want every cent
   that gives us positive ROI. If 0.40 is part of a bulk export, and comes out even, then it's worth doing on principle.")
   - Every item carries its cost to chase: biller minutes x the owner's $/minute (default $1) + fax pages x the fax price per page
     + AI calls x their cost (counted by the app where it uses them), for its cheapest route: alone, or in a batch per payer (one
     letter listing many claims where that payer accepts it; the research in ${S}/lever/ says which do and on what form).
   - Chase when expected value >= cost (break-even included, on principle). Nothing is dropped for size; an item below its cost
     alone is routed into its payer's batch; one still below cost in a batch is shown, counted, and left for a person's choice.
   - The product goal is to drive that cost toward zero: batching, automatic follow-ups and escalations on the payer's own
     deadlines, no AI or person time unless the item needs it. The Results tab shows the cost per item and per dollar recovered.
12. (01:45, THE OWNER: letters must state our leverage; "they can't say no without significant risk"; "AI vs AI".) Letters carry
   only leverage that is TRUE, SPECIFIC and that RemitWatch WILL EXECUTE: the rule that binds this payer (verified citation), the
   date it must answer by, the interest that accrues by statute, the escalation that follows automatically on a stated date (IRE,
   QIC, second-level appeal, state insurance department complaint, external review: owner-approved per kind), the payer's own
   prior payments of the same service as precedent, and requests for the specific basis, the criteria and the reviewer's
   credentials where the law requires them. No bluff, no threat of something the practice will not do, nothing deceptive: a bluff
   an AI reviewer can classify is ignored; a dated escalation that always happens is not. Research: ${S}/lever/ (verified).
13. (02:00, THE OWNER: "We have to win EVERY battle" ... "At least that should be our goal.") The goal is a 100% win rate, pursued
   two ways at once: pick every winnable fight (gate 1), and lose none of them. Every loss is a defect with a post-mortem:
   - every appeal, dispute and escalation records its outcome (won, partly won, lost, no answer -> escalated) with the payer's
     stated reason (read from the payer's answer: rules first, the incoming-fax reader only where the text is free-form);
   - each loss is classed (evidence missing, wrong code or modifier, payer policy, timely filing, criteria not met, wrong payer,
     procedural) and fed back automatically: the pre-send checklist gains the missing evidence, the letter block for that payer and
     issue gets a new version, the pattern's expected value and route are updated, and a pattern that cannot be won under the
     payer's rules is marked so it is not picked again (shown, with why, never silently dropped);
   - the Results tab shows the win rate by payer x issue x letter version, week by week, with every loss one click from its
     post-mortem, so the rate is measured, not assumed.
   Owners: the LETTERS unit (outcomes, escalation ladder, rebuttals, versions) with unit R (win rates on Results) and unit G (track
   record), after the leverage research in ${S}/lever/ lands.
14. (02:10, from the C2 review's high finding; the owner's non-negotiable 2 "every write-off has an owner") Nothing defaults to
   'accept'. ema_checks.never_paid patterns (a payer that paid a code on 5% or less of 20+ answered lines) and any other default
   today written as `... or "accept"` become 'decide' (owner decision pending): listed on the policy page with the payer's paid
   share and counts, shown on Today as "N patterns worth $X wait for your decision", counted every week in "money given up without
   a decision" until decided, and kept out of the billers' Work now (so no wasted work) but never out of sight. Only the owner's
   Accept removes a pattern's dollars from the open totals; Pursue sends its items to Work now. Owners: INT (ema_checks.py, states),
   C2's fixer (the policy page and Today), HOOK (selfcheck's given-up figure), V (checks nothing is accepted without a decision).

15. (03:50, THE OWNER: "is there a way to automatically confirm that the payer received the fax? ... a backup if a fax isn't
    answered 3 times ... don't you think we should be able to beat 42%? The national average is 75% of real, evidence based appeals
    successful, I believe. Our goal is 100%.") (a) Every fax keeps a proof of delivery (pages accepted by the payer's machine, time,
    receiving identifier) and every follow-up encloses it with the rule that set the payer's deadline (CHANNELS P1-P3). (b) No
    appeal dies unanswered: the ladder is fax, second request with proof, switch route (portal, mail), a person's call with a
    script, then the regulatory step the owner approved for that payer type; claim status checks (276/277 through TriZetto) when
    the account allows them. (c) The 42% is the practice's historical share of denied dollars later paid, mostly never appealed;
    it is the 'do nothing' baseline, not a target. Expected value shows both: the baseline and, labelled as an estimate with its
    source, the rate for an evidence-based appeal of that kind, replaced by RemitWatch's own measured win rate per payer x issue x
    level x letter version as decisions arrive (decision 13). The goal is 100% of recoverable dollars, sent on the payer's own
    route and form and followed to the last level. (Amended by decision 16: a low win rate is not a reason to hold back.)
16. (04:05, THE OWNER: "we definitely don't wanna be shy about submitting things with a less than 100% win rate. Even a 5% win rate
    should be appealed no? ... A practice owner doesn't give a shit about burdening insurance they care about their dollars and work
    getting paid for what it's worth.") Appeal whenever expected value beats the cost to send (decision 11), at any win rate: a 5%
    chance at $100 is worth a send that costs cents in a batch. No probability floor anywhere (the same as decision 9 for dollars).
    Two hard limits only: (1) every request is truthful and carries the best evidence on file (never a fact the record does not
    support, which is the line that matters legally, not the odds); (2) the route that wins more for the same money goes first (a
    corrected claim for a coding error, the right payer for a coordination error, then the appeal). A payer pattern (a code a payer
    almost never pays) defaults to 'pursue in a batch per payer' with its expected value and cost shown, not to 'decide' or 'accept';
    the owner can switch it to accept. Volume is not a limit by itself: payers must answer within their deadlines whatever the
    count; RemitWatch measures each payer's answer time and win rate as volume grows, sends the highest expected value first in each
    payer's batch, and paces a payer only when its measured answers slow down, saying so.

## ADDED 2026-11-23 02:00: guardrails by class, not by tester luck (the owner: "I am trusting that your chaos testers will
## uncover everything, or that there are ways to double check all of these so one stray 835 doesn't destroy the whole codebase")
Testers find instances; the release must close classes. The GUARD audit (${S}/guard/brief.md, workflow wf_109ea834-b20) lists every
instance of five classes in the code and writes ${S}/guard/GUARDRAILS.md with owners: L1 one input moves a shared value; L2 trust
at the door; L3 blast radius of one run (the swing guard); L4 every write checks what the person saw; L5 undo and explanation.
Release gate 8 (new): the generated-test gate in GUARDRAILS.md section 3 passes on both hosts (a messy-world generator and a
multi-actor action generator; invariants: money reconciles to the cent, no flood or vanishing past the swing guard reaches
people, no automatic closure without proof rows, a second run changes nothing, no action lands on an item the actor did not
see, no PHI in any log).
Release gate 9 (new): the swing guard is wired in both cycles and holds a generated flood and a generated vanishing, and lets a
rules-version re-detection and a first EMA report through with a notice.
