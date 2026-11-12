# Context checkpoint (resume here after compaction)

Last updated: 2026-11-12 (detection 2.1, living site, alerts and note-based appeal packets built, reviewed and pushed). Read the last section first.

## Mission
Rebuild the Billing-Optimizer repo (currently "DermBill AI", an LLM note-analysis tool) into a
hands-off dashboard that ingests TriZetto ERA (X12 835 ".RMT") files and ModMed EMA exports from
Dropbox, links paid lines back to billed lines, and makes payer downcoding (and other
underpayment) obvious with evidence, dollars, trends, and ready-to-send appeal packets.
Weekly automatic Dropbox pull. No new staff. Owner: Neil Jairath, Owensboro Dermatology
Associates (ODA), drjairath@owensboroderm.com.

## What exists (inputs)
- Repo branch: claude/ecstatic-fermi-orfcgo. Old app = FastAPI + static HTML on Vercel (api/index.py
  -> dermbill.api:app). Old corpus (CPT_Master_Reference.xlsx, rules/, scenarios/) is about note
  coding, not remits. To be replaced.
- Dropbox (connector works, account = Neil Jairath). Root has one mount: "/Trizetto Data"
  (ns:15011251635//). Subfolders: Trizetto (the 30K+ .RMT 835 files), Trizetto.zip (43.5 MB
  archive of the same), Reports, Sept Reports, 9_21-_25, 9_28-10_2, 9-14-26 to 9-18-26,
  10-5-26 - 10-9-26 (weekly EMA xlsx exports + new RMT files), .AU.SUZzP.nosync (ignore).
- RMT filename pattern: P<payerid>D<...>U<835 ctrl>-<check/trace>-<PayerName>-311488176.RMT
  (311488176 is likely the practice's TriZetto/payee id).
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

## Key domain facts (from Cassey Vessels texts + prior dashboard)
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
- EMA exports: Claim ID (e.g. CB00EAJD9C017) == 835 CLP01. REF*6R is NOT the EMA Bill Item Identifier (different
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
- Scratch store with everything parsed: /tmp/claude-0/-home-user-Billing-Optimizer/96e81489-2ad2-5c70-991d-e00db2e22749/scratchpad/data/remitwatch.sqlite
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
