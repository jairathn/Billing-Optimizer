# Context checkpoint (resume here after compaction)

Last updated: 2026-11-12 (detection 2.1, living site, alerts and note-based appeal packets built, reviewed and pushed). Read the last section first.

## Mission
Rebuild the Billing-Optimizer repo (currently "DermBill AI", an LLM note-analysis tool) into a
hands-off dashboard that ingests TriZetto ERA (X12 835 ".RMT") files and ModMed EMA exports from
Dropbox, links paid lines back to billed lines, and makes payer downcoding (and other
underpayment) obvious with evidence, dollars, trends, and ready-to-send appeal packets.
Weekly automatic Dropbox pull. No new staff. Owner: the practice's physician-owner.

## What exists (inputs)
- Repo branch: claude/ecstatic-fermi-orfcgo. Old app = FastAPI + static HTML on Vercel (api/index.py
  -> dermbill.api:app). Old corpus (CPT_Master_Reference.xlsx, rules/, scenarios/) is about note
  coding, not remits. To be replaced.
- Dropbox (connector works, the owner's account). Root has one mount: "/Trizetto Data".
  Subfolders: Trizetto (the 30K+ .RMT 835 files), Trizetto.zip (43.5 MB
  archive of the same), Reports, Sept Reports, 9_21-_25, 9_28-10_2, 9-14-26 to 9-18-26,
  10-5-26 - 10-9-26 (weekly EMA xlsx exports + new RMT files), .AU.SUZzP.nosync (ignore).
- RMT filename pattern: P<payerid>D<...>U<835 ctrl>-<check/trace>-<PayerName>-<practice id>.RMT
  (the last part is the practice's own id with TriZetto).
- EMA export families (xlsx): "Encounter and Charge Lines" (42 cols, billed CPT per line),
  "ERA and Payment Line Adjustments" (24 cols), "Claim and Claim Status" (23 cols); annual
  2024/2025/2026 files live in Reports/; weekly pilot exports in dated folders.
- Uploaded zip = scrape of a prior ChatGPT-built "ODA financial integrity" dashboard (Oct 12 2026).
  Extracted to scratchpad zip_extract/. Its findings: 33,615 RMT files -> 32,617 unique 835s,
  193,043 CLP claim events, 419,389 SVC lines; 835 CLP01 -> EMA Claim ID matches 94%;
  835 SVC -> EMA charge exact-composite 72% (90% in recent cohort);
  116 claims where SVC06 (submitted E/M) > SVC01 (paid E/M) = downcoding candidates it refused
  to confirm without 837s; 10,724 CARC 59 lines; monthly 835 primary payments ~0.5-1.5M.
  Payer mix: Medicare KY J15, BCBS/Anthem, Humana Medicare, UHC (several), Medicare IN J8,
  UMR, Optum, Cigna, supplements. 3 locations, ~10 providers.

## Key domain facts (from the billing lead's texts + prior dashboard)
- Downcoding does not appear as a denial. 835 shows paid code in SVC01 and the submitted code in
  SVC06 when they differ; CAS adjustments carry CARC (e.g., 150, 186, 16, 97, 45, 59) and RARC
  (e.g., N22, M127, N657, N381) that hint at it. Must compare billed vs paid per line.
- Billers were adjusting these off as "insurance adjustments" instead of appealing.
- Payers stop for a while after enough successful appeals, then resume months later.

## Decisions so far
- Treat EMA Encounter/Charge export + 835 SVC06 as sufficient evidence of downcoding (two
  independent sources agree on what was billed). 837s are nice-to-have, not a blocker.
- Keep Python (FastAPI) backend; add local store (SQLite/DuckDB); dashboard as static web app.
- Hands-off: the deployed app needs its own Dropbox app credentials (refresh token), not the
  Claude connector, for weekly pulls.


## Data findings so far (from 32,215 archived 835s, payments 2024-01 to 2026-09; parser remitwatch/x12_835.py, store remitwatch/store.py)
- Parsed 31,627 unique 835 transactions, 188K claims, 409K lines in 59 s; 0 parse problems; 28 unbalanced txns.
- Classic E/M downcoding (SVC06 original > SVC01 paid) = 155 lines, est. allowed shortfall ~$3.7K, 145 of them Anthem.
  Charge-amount inference (remitwatch/fees.py) agrees with SVC06 in 151/155 and finds only 1 hidden case. Rate-level
  downcodes (allowed = lower level's rate) = 0. CONCLUSION: code-level downcoding is NOT the big leak at ODA.
- Big leaks found instead:
  1. Anthem commercial PPO (cfi 12, 42K claims, $3.4M paid) allows 99213 ~$51-53 and 99214 ~$64-66 in 2025 = 61-64% of
     KY Medicare; procedures at 106-109%, 88305 at 153%. UHC pays 108% of Medicare for 99214. Contract problem.
  2. MIPS (CARC 144, negative = bonus): 2024 +$308K (~6.5%), 2025 +$82K (1.6%), 2026 +$13.5K (0.5%). Bonus collapsed.
  3. Medicare takebacks (PLB WO): KY Medicare $58K (2024), $130K (2025), $165K (2026 YTD); Medicare IN $47K/$71K.
  4. E/M bundled (CO-97/B10/236) ~1,000+ lines/yr; many with NO modifier 25 or with modifier 57 (correctable errors).
  5. Radiation therapy (77401/77280/77600/77436/G6001) line denials: auth (197), frequency (151), duplicate (18), B7.
  6. Anthem claim-level denials: 1,765 claims ($566K charges), 1,095 never paid.
  7. Zelis "transaction fee" PLB CS $47.8K + origination fees (AH) $20K over the period; ~$30K/yr skim.
  8. G2211: commercial payers (Anthem, UHC, UMR, Cigna) deny nearly all; Medicare denies when billed with mod 25.
  9. CARC 59 MPPR: no reductions on add-on codes found; KY Medicare ~$295K/yr reductions, looks rule-consistent.
- Medicare below-usual-rate E/M lines are explained by PA/NP 85% payment, not underpayment (stratify by rendering NPI).
- EMA exports: Claim ID (e.g. CB00ABCD1C017) == 835 CLP01. REF*6R is NOT the EMA Bill Item Identifier (different
  number ranges). Line matching must be composite (claim id + DOS + CPT + modifiers + units + charge).
- Annual EMA exports (ODA - 2024/2025/2026.xlsx etc.) are NOT in Dropbox; only Jul 27 2026 onward exists. Missing data.
- 837 claim files exist: 87 PROFESSIONAL_Claims_*.txt in 10-5-26 - 10-9-26 (Oct 5-9 2026 only).

## Progress log
- [x] Scouted repo, zip, Dropbox root
- [~] Deep research workflows running (wf_00156c6f-279 domain, wf_ee7fd1ce-70c tech) -> docs/RESEARCH_DOMAIN.md, docs/RESEARCH_TECH.md
- [x] Schema discovery on real files (see remitwatch/ema_exports.py)
- [x] Architecture (ARCHITECTURE.md), README, docs/OPERATIONS.md, docs/MISSING_DATA.md, docs/DATA_FINDINGS.md
- [x] Parser, store, EMA loader, fee inference, detection engine v1.1, API, dashboard, appeals, Dropbox sync, weekly job, tests (8 passing)
- [x] Dashboard (remitwatch/static/index.html) verified in a headless browser against the full store
- [x] Scheduler: cli weekly + docker compose cron + crontab example; Dropbox refresh-token client
- [ ] Commit/push

## Run book for this session (where files live)
- Scratch store with everything parsed: <scratchpad>
- Dropbox mirror + unzipped archive: .../scratchpad/data/dropbox and .../scratchpad/data/zip_extract
- Rebuild: python -m remitwatch.cli --db $DB ingest-835 <dir>; ingest-ema <dir>; detect; serve
- Regenerate findings doc: python scripts/leak_report.py --db $DB > docs/DATA_FINDINGS.md
- Pending: recent RMT/837 files from the download agent (1,399 + 87), research docs from the two workflows.

## Headline numbers (archive through 2026-09-10, detection v1.1)
- Total detectable leakage ~$3.26M (2024-2026). Downcoding $61K (Anthem 99204->99203 x108, 99214->99213), bundling $435K,
  denials never paid $1.18M, contract gaps $877K at Medicare parity (Anthem commercial E/M 59-72% of Medicare = $616K;
  MA below Medicare $261K), underpayment vs own rate $183K, fees $67K (~$30K/yr), takebacks $460K.
- Skin substitutes (Q-codes): Medicare paid ~$2.5M in 2025; reversals of ~$1.4M charges / ~$700K paid; takebacks likely tied.
- MIPS bonus: +$308K (2024) -> +$82K (2025) -> +$14K (2026).
- Still appealable now (deadline not passed): ~1,339 findings, ~$146K; expired ~$1.2M.
## Pending when this checkpoint was written
- Download agent (ac41a9a060e3b3cc7): recent RMT (1,399) + 837 (87) files into scratchpad/data/dropbox; then: ingest-835 + ingest 837s, detect, regenerate docs/DATA_FINDINGS.md, commit.
- Workflows wf_00156c6f-279 (domain) and wf_ee7fd1ce-70c (tech): write docs/RESEARCH_DOMAIN.md and docs/RESEARCH_TECH.md; then integrate
  deadlines/statutes into appeals.py and detect.py APPEAL_DAYS, update OPERATIONS.md hosting recommendation, commit.

## Update after reading the prior app's source (ODA-Financial-Dashboard-Source)
- Adopted: payload fingerprint excludes ST/SE control numbers; supersede key includes payment date; composite 835->EMA charge
  linkage (remitwatch/link.py: exact_composite / fallback_candidate / ambiguous / unmatched) and ERA handling join (line_handling);
  claim ids = 9-char bill id + suffix (C017/C024/C031 = submission sequence), so later-payment checks key on the bill prefix.
- New: findings auto-resolve when a later remit paid the same bill/code/DOS (status 'resolved', note 'Paid on a later remittance');
  ~5,540 findings / ~$816K were rework the billers did do. Open leakage in downcoding+bundling+denial+underpayment ~ $1.03M.
- EMA link coverage for DOS >= 2026-07-27: ~87% exact composite (matches the prior app's 90%).

## State at 2026-11-06 ~15:45 UTC (commit 5c92313)
- Store now holds 32,043 remits (payments through 2026-10-09) after ingesting 496 new recent RMT files; 101 recent files were
  byte-identical to archive members. 837 .txt files are routed to x12_837.ingest_837_file by store.ingest_835_paths; the
  835 parser skips non-835 ST sets. Recent cohort (DOS 2026-09-14..10-02) links 1,430 exact / 1,646 (6 ambiguous) after
  collapsing identical charges repeated across overlapping daily/weekly EMA exports.
- Open leakage (open findings only) ~$2.47M; recovered on later remits ~$816K (auto-resolved; rule reversible).
- Still pending: remaining recent RMT (Sept Reports/Trizetto files ~600+, rest of 10-5 folder incl. 87 x 837 .txt) from the
  download agent; research docs from both workflows (then: appeal deadlines/statutes into appeals.py + detect.py
  APPEAL_DAYS, OPERATIONS.md hosting recommendation, final summary to the user).


## State at 2026-11-12 (third request delivered)

The third request asked for: breakdowns by provider, NPI, procedure, code and anything else useful; a living site that pulls from
Dropbox continuously and shows progress in the fight; alerts when downcoding ramps again; and a small feature to upload a clinical
note and produce an appeal report to email. All four are built, tested (34 tests), independently reviewed (two review agents, 19
verified defects, all fixed) and pushed to claude/ecstatic-fermi-orfcgo.

New modules
- remitwatch/alerts.py    AlertEngine: RAMP, RISING, RESUMED, NEW_BEHAVIOR, PROVIDER_TARGETED, RATE_CUT, NEW_REASON_CODE, DEADLINE, STALE_FEED
- remitwatch/pipeline.py  run_cycle (sync, ingest, detect, alerts, notify, report), owned lock with heartbeat, Scheduler thread, status()
- remitwatch/notes.py     extract_text, scrub, review_note (Claude API, structured output), build_from_note, render_pdf, email_packet
- remitwatch/static/features.js   Alerts, Who & what (explorer), Progress, feed panel, note-based appeal builder
- tests/test_features.py  19 tests for all of the above (synthetic data)
Changed: detect.py (version 2.1), codes.py (appeal_rule, LEGAL, payer_group_kind, pos_class), appeals.py, api.py, cli.py (cycle, alerts),
report.py, x12_837.py, index.html, deploy files, docs.

Things a future session must know
- Reason code 186 is Anthem's downcoding mark. Commercial: with SVC06. Medicare Advantage and Medicaid: billed code kept, allowed
  amount cut to the next level down (signal DOWNCODE_LEVEL_REDUCED).
- Anthem Kentucky commercial reconsideration window is two years from the EOP (read in the manual). This took Anthem's still-appealable
  open findings from 224 ($18.7K) to 1,498 ($83.9K).
- Rate baselines are keyed by place-of-service class. Facility-setting visits are paid less and must not be compared with office rates.
- Findings keep recorded work across runs: notes and auto_note are separate columns, prev_status allows exact undo, work follows the
  line when its signal changes, stale rows are excluded from totals.
- The live Claude API call in notes.review_note was never run (no API key in the build environment). It is tested with a stand-in client.
- The newest remittance in Dropbox is dated 2026-10-09. The feed has stopped or is uploaded monthly. The STALE_FEED alert says so.
- docs/RESEARCH_SUMMARY.md lists exactly what was checked against a primary source. The two RESEARCH_*.md files are raw, mostly
  unverified notes salvaged from research runs that ran out of usage credits. Be frugal with agents: the user ran out of credits twice.

Numbers on the full archive (32,550 remittances, payments 2024-01-01 to 2026-10-09, $30.0M paid)
- Open leakage $2.40M: contracts below Medicare $897K, denials $633K, fees and takebacks $531K, bundling $162K, underpayment $128K,
  downcoding and code changes $52K.
- Recovered on later remittances: $1.16M across 5,574 lines. Open and inside the filing window: 3,236 findings, $254K.
- Visit downcoding: 229 of 93,878 office visits (0.24 percent), $7.7K. Anthem commercial 122 (0.40 per 100), Anthem Medicare Advantage
  85 (2.63 per 100). 99204 paid as 99203 is half of it. One provider carries 44 percent. A quarter is out-of-state Blue plans.
- Anthem recoding of radiation codes since April 2026: 126 lines, $43K. This is the 2026 "ramp".
- Active alerts at build time: feed stopped; one provider singled out by Anthem Medicare Advantage (8 of 13 level 4 and 5 visits in
  90 days); Humana Medicare Advantage bundling climbing (3.3 percent of lines against 1.7); Anthem procedure code changes climbing;
  340 findings worth $24K reaching their deadline within 30 days.

What the user still has to do (see docs/OPERATIONS.md)
1. Put it on a BAA-covered host (small AWS, Google Cloud or Azure VM, 4 GB) with docker compose.
2. Create the Dropbox app, run `remitwatch.cli oauth`, set the three DROPBOX_ variables and REMITWATCH_SYNC_INTERVAL_MINUTES, add the webhook.
3. Set SMTP_HOST and REPORT_TO for alerts and the summary.
4. Get a BAA from Anthropic before setting ANTHROPIC_API_KEY for the note review.
5. Restart the remittance uploads, and route 837 claim files and annual EMA exports to the same Dropbox folder (docs/MISSING_DATA.md).

Scratch (session only, not in the repository): store at <scratchpad>/data/remitwatch.sqlite, Dropbox mirror at <scratchpad>/data/dropbox,
payer manuals at <scratchpad>/manuals, statutes at <scratchpad>/statutes, screenshots at <scratchpad>/shots.

## State at 2026-11-12, later (Vercel mode; pushed to main)

The user said a push to main deploys automatically (a Vercel project whose production address is its vercel.app domain, named
after this repository; most likely the Hobby plan: 2 GB, 300 s, one scheduled run a day) and asked for an instruction sheet
for an intern. The app now runs there with its store in a private Dropbox folder. Read docs/VERCEL_SETUP.md first.

- New: remitwatch/cloud.py (the serverless cycle), state.py, actions.py, runtime.py, static/setup.html, tests/test_cloud.py,
  tests/fakes.py (FakeDropbox), scripts/cloud_bench.py, public/robots.txt, .vercelignore. vercel.json rewritten (framework null,
  fluid, functions maxDuration 300, catch-all rewrite, one cron). pandas removed (ema_exports reads with openpyxl).
- The intern sheet is a Claude Doc titled "RemitWatch on Vercel: setup sheet" (private to the user). The repository is PUBLIC:
  no secrets, no provider-level figures, no site-specific addresses in committed files.
- Proven on the real archive through a local stand-in for Dropbox: first load in two runs, 1.42 GB peak, results equal to the
  disk-based store to the cent (17,407 findings, open $2,403,995, recovered $1,330,153). NOT proven: the real Vercel runtime, real
  Dropbox writes, the webhook from Dropbox, the live Claude note review.
- Found and fixed while proving it: unmatched-line shortcut dropped biller handling (link.py); scratch-file prefixes leaked into
  source names; an idle run rewrote the whole store; a due weekly report could repeat; order-dependent ties (usual_amount, top_of,
  mark_superseded now breaks ties by file date then key).
- The setup flow the user's intern follows: create a Dropbox app (Scoped, Full Dropbox, three file permissions), add
  REMITWATCH_PASSWORD, CRON_SECRET, DROPBOX_APP_KEY, DROPBOX_APP_SECRET in Vercel, redeploy, /setup, Connect Dropbox, add
  DROPBOX_REFRESH_TOKEN, redeploy, Run the first load, add the webhook.
- GitHub Pages is also enabled on this repository and rebuilds from main on every push.
- To check a deployment from a session: GET /api/health on the production address (no login) shows version, commit and runtime.
  Commit statuses are not readable without a pull request.

Review of the Vercel mode (same day). An independent reviewer reproduced, and the following commits closed: a self-call loop on
one unreadable file (and two other loop routes through notifications); status changes dropped when the dashboard's copy was ahead
of the store (the store is now saved first, with a version marker in serving.json); alert mail and the weekly report repeating
(mail now goes after the saves, markers in notified.json keyed by the alert's first_seen); pages waiting behind Dropbox retries
(reads use a one-attempt client, writes a patient one); a partly applied bulk change not saved; a self-test that could fail for
good under simultaneous first requests; an empty dashboard after one failed storage call on a cold start. The reviewer's
reproduction scripts are in the session scratchpad under rv_cycle. Production checks after each push: /api/health (commit,
self-test), / redirects to /setup, data routes 503 until a password is set, /api/cron/cycle 401 without the secret, webhook
challenge echoed, no repository file served.
Known and accepted: a run killed by the platform (memory or the 300 s limit) leaves its lock for seven minutes and is not
restarted by anything but the daily run or the button; SQLite 3.40 on Vercel (no newer SQL is used; building 3.40 locally to
test against it was not permitted in the session, the live self-test passes).
Proven on the live Vercel deployment (no credentials needed): the build and routing (framework null, catch-all rewrite keeps the
original path, nothing from the repository is served), Python 3.12.16 with SQLite 3.40.0, about 2.2 GB of memory, the self-test
(store in memory, grown, saved, reopened, dashboard copy built; about 120 ms, faster than the test machine), and that a request's
work finishes after the caller hangs up (a temporary probe, since removed). Still unproven until the settings are added: real
Dropbox reads and writes, the first load on Vercel, the webhook from Dropbox, mail, and the live Claude note review.

## Provider view (November 12, 2026)

(First version. The fold, the EMA pull time and several measures were changed the next day: see the section after this one.)

The user asked whether the same data can show what each provider is doing, and pasted a productivity memo from another assistant
built on one EMA charge export. Answer: yes, for almost three years of insured visits. Provider-level figures are NOT written in
this repository (it is public); they are in the password-protected dashboard and in the conversation.

- New: `remitwatch/activity.py` (tables `provider_roster`, `provider_days`, `provider_services`, `provider_payers`,
  `provider_panels`, `provider_cohorts`, `provider_ema_days`, `provider_ema_lag`; `activity:info` in `meta`),
  `remitwatch/static/providers.js` (the Providers tab), `tests/test_activity.py`, endpoints `/api/activity/overview|trend|recent`
  and `/api/activity/provider/{id}`, CLI `activity`. Built after detection in both pipelines (`activity.refresh`, never fatal), copied
  into the dashboard's copy (`cloud.SERVING_TABLES`; `serving.json` now carries a `build` number so a copy made by another build is
  remade). `ACTIVITY_VERSION` bumps drop and rebuild the tables.
- Method, in order of how much it matters:
  1. A bill is the first nine characters of CLP01. The same billed line returns on several remittances; lines fold into one service
     by REF*6R, and payers that omit it are matched on date, billed code and charge. Allowed is the newest primary decision (a
     reversal voids the earlier one; a later duplicate denial does not). Paid is every insurer payment added up.
  2. The clinician is the rendering NPI when it is a person. Supplement and secondary payers send the group NPI with each
     clinician's name beside it, so an NPI seen under many names is the group; then the name, the line-level provider reference,
     another remittance for the same bill and last the EMA export decide. About 3 percent of encounters stay unassigned.
  3. Encounters are typed. Pathology is billed on its own bill under the physician who read the slide and radiation as a
     treatment series; neither is a patient seen. Counting bills instead of typed encounters overstates physicians' volume.
  4. Patients seen per clinic day uses days with at least five patients, median and middle half. Two bills for one person on one
     day are one patient (the name is used in memory only; nothing about a patient is stored).
  5. Skin substitute products and drugs are kept out of "allowed per patient seen": they are paid mostly to cover their cost.
  6. Panels (distinct patients in twelve months), the share of the previous year's panel seen again, new-patient return within 90
     and 365 days, and patients biopsied who had a skin cancer treated here within 120 days (a floor: treatment elsewhere is
     invisible) come from following each patient through the store.
  7. Biopsy rates are shown within Medicare and within everyone else, because age drives biopsies and payer is the only proxy.
- What was learned about the inputs: the EMA charge exports are truncated by billing lag (a visit is absent until its bill
  exists, and the lag differs widely between clinicians), which is why a one-month export made some clinicians look half as busy
  as they are. The later weekly exports come in a thin layout with no bill date, bill id or patient id, pulled as the week ends, so
  none of their days can be shown as captured. Remittance lag: median 19 days, 90 percent within 37, 98 percent within 62.
- Measured on the real archive: the build takes about 7 seconds and adds no memory. A routine re-analysis peaks at about 1.5 GB;
  the 1.42 GB written earlier was a first load, which peaks lower. Docs corrected.
- Not in any file: schedule fill and no-shows (needs the EMA appointment export), staff cost, pay terms. Listed in MISSING_DATA.md.

## Provider view, second pass (November 13, 2026; activity version 2)

Five independent reviewers checked the first version: one recomputed every per-clinician figure from the raw tables, one argued
against the conclusions drawn from them, one tested the method, one read the engine and one drove the screen and checked the diff
for leaks. The per-clinician counts held (within 5 percent everywhere). What changed as a result:

- **The fold** (`activity.py`, `_fold` and `_settle`). About one service in ten was counted twice, because a second payer often
  returns its own line control number or its own charge for the same billed line, and a reversal from any payer wiped what another
  payer had allowed. Lines now fold per billed line across payers (control number within a payer, then date and billed code), each
  payer's decision is kept apart, and the amount allowed is the first payer's that still stands. Two checks are stored with every
  build (`activity:info`, key `checks`): visit codes per patient visit (1.004 on the archive) and insurer paid over allowed by
  family and quarter.
- **Typed contacts.** A patient-day whose only service is a post-operative check, a light-box session or a staff injection is no
  longer a patient seen (`other_contacts`). More biopsy codes count as biopsies (nail, lip, ear, eyelid and similar sites).
- **Office state.** Each clinic day carries the state of the Medicare contractor that paid its Medicare claims (`place`), which
  follows the office. Every clinician with days in two states can be compared between them.
- **Who comes back.** `provider_panels` now holds kept by anyone, kept among established patients only, and kept by the same
  clinician. The first figure mostly measured how many first-time patients a clinician had the year before.
- **What biopsies find.** Only a first biopsy counts (none in the 120 days before, no cancer treated in the year before), and the
  screen prints the fixed period it covers. It stays a floor and is not a quality measure; pathology results are.
- **Missing payer weeks** (`find_gaps`). Weeks in which a payer that pays weekly sent under a quarter of its usual claims and then
  resumed are found, listed on the Data tab (`/api/coverage`) and subtracted from the completeness of the months they would have
  paid. Five such spans exist in the archive (listed in MISSING_DATA.md, item 2).
- **EMA pull time.** An export's pull time is the workbook's creation time (`ema_exports.export_created`, table `ema_sources`,
  recorded at ingestion), not a guess from its contents. A later export moves a day's pull date forward even when all its rows are
  repeats, and an export by transaction date covers no day. This released several weeks the first version held back as "waiting".
- **Screen** (`static/providers.js`). Answers are kept in `state.pv.data` and a change of filter, metric, theme or pick redraws
  from memory without moving the reader; a late answer can no longer draw over a newer one; month boxes ignore half-typed values;
  fixed-period columns say so; the call-back table no longer depends on the window; charts whose canvas has gone are released.
- **Owner's password.** `REMITWATCH_OWNER_PASSWORD` (optional) keeps what each clinician is doing for the owner. It is typed into
  the Providers tab, kept in the page's memory, and sent in `X-Owner-Password` with `/api/activity/*`, `/api/breakdown` and the
  rate routes. Without it the activity routes answer 403, and the breakdown and rate routes leave out line counts wherever a cell
  is four fifths or more one clinician's work (the provider dimension, and stand-ins such as a provider type with one person in
  it). Wrong tries have their own brake and are tried with one call. The page forgets the password on Lock (also in the page
  header), on leaving the page (so the browser's Back cannot bring it back open), on reload and after half an hour; the box is
  marked so that password managers leave it alone. Two designs were thrown away on the way: a second Basic login at `/owner`
  (it signed billers out in Chromium and stayed in the browser), and hiding only the provider dimension (one physician alone
  in the "unknown" provider type still showed). It is a screen for the dashboard, not encryption: the state folder in Dropbox
  holds the same tables, and the docs say so.
- The practice's own name, address, tax ID and NPI are no longer defaults in `appeals.py`; they are `PRACTICE_*` settings, and
  letters show bracketed words until they are set. `/setup` lists them.
- **Speed.** The readers pull thousands of rows into Python and crawled when several ran at once (six together took about seven
  seconds each on the archive, against 0.05 alone). The API now runs one at a time (`_ACTIVITY_LOCK`).
- A second review (three reviewers: an independent recomputation, the gate and a leak check, the screen) found the engine sound
  (every per-clinician figure within 0.15 percent of an independent derivation) and asked for refinements, now in version 3:
  missing payer weeks are measured by payer and month of service instead of spread by the lag curve (the first method put the hole
  in the wrong month); a payer keeps every decision and a reversal cancels the one it matches; payers are told apart by payer id;
  a payer that writes 0 for units billed no longer hides units; a line with no allowed amount counts what was paid plus the
  patient's share; one Medicare claim is enough to give a clinic day its state; EMA patients are typed like remittance patients;
  an export that holds a sliver of a day does not vouch for it.
- `ACTIVITY_VERSION` is "3" and `SERVING_BUILD` "4". Measured on the simulated Dropbox with version 2: a site holding state from
  the previous release rebuilds its provider tables in one run of 27 seconds (peak 1.33 GB, no detection); a first load from
  nothing takes two runs (54 and 36 seconds, peak 1.43 GB). The cloud-built store and the CLI-built store differ by two findings
  in 17,400 and about a hundred dollars, and four clinicians' paid totals differ by a few hundred dollars: eight pairs of duplicate
  remittances from October and November 2024 survive as different copies depending on the order files are read in. Known, small,
  not fixed.
- Older files held real identifiers and were cleaned in this pass (tests, research notes, this file): two claim ids, a bill item
  id, the practice's tax id, payment trace numbers, one clinician NPI, the owner's name and work email, a staff member's name, a
  Dropbox namespace id. They remain in the repository's history. The owner decides whether to rewrite it or make the repository
  private.

Things a later session should not redo or get wrong:

- Provider-level figures, clinician names and office names stay out of this repository. Tests use made-up people.
- Do not compare clinicians on "kept from the year before" without the established-only column, on biopsy yield across offices, or
  on a recent EMA export without checking when it was pulled.
- The memo the owner pasted was right about its own arithmetic and wrong about two clinicians because its export was pulled three
  days after the period ended. The pattern in the data is an office effect, not a clinician effect.
- Still not built: the EMA appointment export (no sample exists yet), pathology results, cost per clinician.
- Known and left: the Who and what denominators count remittance lines, not folded services, so a line two payers report
  counts twice there; the Providers tab counts each service once. Rates by provider on the two tabs differ by a few percent.
- Known and left: on a VM behind Caddy the brakes now count per visitor (uvicorn trusts the proxy header); on Vercel the
  address is the caller's as Vercel reports it. Someone on the owner's own network can still hold the owner's tab closed.
