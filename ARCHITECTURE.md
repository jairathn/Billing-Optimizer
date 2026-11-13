# Architecture

## Pipeline

```
Dropbox "/Trizetto Data"                    local mirror                 SQLite store                 dashboard / report
  32K+ .RMT (X12 835)  ──list_folder──▶  data/dropbox/...  ──parse──▶  transactions, claims, lines,  ──detect──▶  findings ──▶  API + UI
  EMA xlsx exports        (cursor delta)                      load       adjustments, remarks, plb,                 payer_rates     weekly email
  837 claim files (.txt)                                      read       ema_rows, claims_837, lines_837            fee_table       appeal packets
```

One process, one SQLite file. A cycle (`remitwatch.cli cycle`, or the scheduler inside the dashboard) runs sync, ingest, detect, alerts and notify in order and is safe to re-run: files are skipped by content hash, 835 transactions by content fingerprint, EMA rows by row fingerprint, and findings keep their status, notes and appeal dates across runs.

## Parsing

`x12_835.py` reads 005010X221A1 835s: ISA/GS envelopes (several per file are handled), BPR/TRN payment identity, 1000A/B payer and payee, 2000 LX, 2100 CLP with NM1/REF/DTM/AMT/MOA/MIA, 2110 SVC with DTM/CAS/REF/AMT/QTY/LQ, and PLB. Money is integer cents. Each line keeps the adjudicated code (SVC01), the submitted code when reported (SVC06), modifiers, charge, paid, allowed (AMT B6), units, service date, the line item control number (REF 6R), and all CAS adjustments and RARCs. Balancing is checked at line, claim and transaction level. A content fingerprint of ST..SE lets the store ignore the same remittance delivered under a new file name, and `mark_superseded` flags older versions of a trace number that was re-delivered with different content.

`x12_837.py` reads 837P claim files (CLM, SV1, DTP 472, REF 6R) so paid lines can be compared with submitted lines even when the payer omits SVC06.

`ema_exports.py` maps every known ModMed EMA export layout (annual and weekly, 12 to 42 columns) to one canonical row shape. The Claim ID in EMA (for example `CB00ABCD1C017`) is the 835 CLP01, which is how remits join to the practice management system. REF 6R in the 835 is not the EMA Bill Item Identifier; line matching uses claim id, date of service, code, modifiers, units and charge.

## Baselines (no contracts needed)

- Provider tier: each rendering NPI is classified physician or non-physician from its Medicare allowed amounts (non-physician practitioners are paid 85 percent). Provider names are the commonest personal spelling across payers.
- Fee table: the practice's charge amounts per code and year, learned from unaltered lines. A charge that equals the fee of a higher-level code reveals a downcode even when the payer hides the original code.
- Payer rates: per payer group, year, code, tier and place-of-service class (office or facility), the most common allowed amount on cleanly paid primary lines, using only the highest-ranked surgical procedure on each claim. Lines with pricing modifiers, bundling codes or level-of-care codes are left out. Facility-setting visits are paid less by design, so they have their own baseline and never read as underpaid against the office rate.
- Medicare baseline: the same rates from Medicare KY (CGS J15), falling back to Medicare IN.
- Multiple procedures: each payer's own routine allowance for the second and for later same-day procedures is learned from its remittances (snapped to 100, 75, 50 or 25 percent). A payer whose routine is below the standard 50 percent gets one contract-level finding instead of hundreds of line findings.
- Expected amount for a downcoded line: the payer's usual rate for the billed code; where the payer has no baseline for that setting, the amount actually allowed scaled by the payer's office-setting ratio between the two codes.

## Detection

`detect.py` loads all non-superseded lines with their claim and transaction context, builds the baselines, then runs line rules, claim rules and aggregate rules. Visit downcoding has six detectors that cover the ways a payer can do it:

| detector | what the remittance looks like |
|---|---|
| DOWNCODE_SVC06 | the payer reports the billed code and pays a lower level |
| DOWNCODE_VS_BILLED | no change reported, but the 837 claim file or EMA shows a higher level went out |
| DOWNCODE_CHARGE | no change reported, but the charge is the practice's fee for a higher level |
| DOWNCODE_LEVEL_REDUCED | billed code left in place, payment cut with reason code 186 or 150 |
| DOWNCODE_RATE | billed code left in place, allowed amount equals the payer's rate for a lower level |
| DOWNCODE_LINE_SWAP / DOWNCODE_LEVEL_DENIED | billed visit denied for level, with or without a lower-level line paid in its place |

One event produces one finding: the paid half of a line swap, and a secondary payer that echoes the primary's code change, are not counted again.

Each finding carries the billed code, the billed-to-paid pair, the number of levels dropped, the plan named on the remittance, place of service, location and diagnosis where EMA has them, the payment month, the filing date and the basis for that date. `exposure` holds the number of lines billed by month, payer, plan, provider, billed code and place of service, which is the denominator behind every rate in the dashboard.

Findings are rebuilt on every run and matched to the previous run by id. What people recorded is carried forward. A later remittance that pays the same bill, code and date resolves the finding and records the amount; the biller's own note is kept beside the automatic one, and if that payment is later withdrawn the finding returns to exactly the status it had. When new data changes a line's signal, the recorded work follows the line. Findings someone worked that the rules no longer produce are kept and marked stale, and stale rows are left out of every total.

## Alerts

`alerts.py` runs after detection. It compares the last 28 days of payment dates, counted back from the newest payment in the store, with the 182 days before them, per payer and per family (visit downcoding, procedure code changes, bundling, denials, underpayment), using day-level line counts as denominators. A ramp needs at least four events, a rate at least twice the baseline rate, and a Poisson tail probability below one percent. A slower rule compares the last 91 days with the 365 before them, for a climb too gradual to double in four weeks. A payer with no events in the baseline but a history before it is reported as started again. Other rules: one provider singled out (level 4 and 5 visits, 90 days), rate cuts (the payer's most common allowed amount over the last 60 days against the 180 before), new reason codes, deadlines and a stopped feed. An alert has one row for as long as its condition holds, clears itself, and is raised again if the condition returns.

## Appeal packets

`appeals.py` writes the letter for each signal from templates, with statute wording that was checked against the Kentucky and Indiana sources (`codes.LEGAL`) and filing windows from `codes.appeal_rule`. `notes.py` adds the clinical part: it extracts text from the uploaded note, removes the identifiers known from the remittance and common identifier patterns, and asks Claude (`claude-opus-5-5`, structured output) to audit the note against the CPT rules for the dispute and return levels, word-for-word quotes, gaps and a recommendation. The paragraphs go into the letter unless the recommendation is not to appeal. The packet is rendered as one PDF with reportlab and stored in `appeal_packets`. Without an API key the same packet is built from the template.

## Provider activity

`activity.py` runs after detection and answers a different question from the same lines: what each clinician did. It writes its own small tables (`provider_roster`, `provider_days`, `provider_services`, `provider_payers`, `provider_panels`, `provider_cohorts`, `provider_recall`, `provider_ema_days`, `provider_ema_lag`) and never fails the run that called it.

- A bill is the first nine characters of CLP01. Every remittance line for a bill is folded into one service per billed line: by the line control number within a payer, and across payers by date and billed code, because a second payer often returns its own numbers or its own charge. Each payer's decisions are kept apart and in order (a payer is its payer id, whatever name it writes under); the amount allowed is the newest standing decision of the first payer to decide, a reversal cancels only the decision it matches, a payer that states no allowed amount counts for what it paid plus what it left to the patient, and paid is every insurer payment added up.
- The clinician is the rendering NPI when it is a person; an NPI seen under many names is the group. After that the line-level provider reference, the name, another remittance for the same bill and last the EMA export decide.
- Encounters are typed: a visit or procedure is a patient seen; a slide read on its own bill, a radiation treatment, a post-operative check and a nurse-only visit are counted apart. A clinic day is a day with at least five patients seen, and carries the state of the Medicare contractor that paid that day's Medicare claims.
- Panels (distinct patients in twelve months), who came back, new-patient return and what first biopsies led to come from following each patient through the store by name. The name is used in memory and never stored.
- Completeness: the service-to-payment lag curve is measured from the store and a month counts as complete at 97 percent. Weeks in which a payer that pays weekly sent next to nothing and then resumed are found, and the hole is measured, not modelled: in the months of service those weeks would have paid, the payer's claims are compared with its usual share of everyone else's, and a month more than a fifth short has the difference taken off its completeness.
- The EMA charge exports are a second source for recent weeks. Each export's pull time is read from the workbook (`ema_sources`), and a day counts only when an export covering it was pulled late enough for that clinician's bills to exist.
- `ACTIVITY_VERSION` changes whenever the tables or the way they are filled change; they are then rebuilt once, without re-running detection. The API runs one reader at a time (`_ACTIVITY_LOCK`): several at once are far slower than the same calls in turn.

## Serving

`api.py` exposes JSON endpoints: summary, findings with filters and CSV export, finding detail, payer rates, `breakdown` (any one or two of 23 dimensions with denominators), `progress`, `alerts`, `sync/status`, `sync/run`, the Dropbox webhook, the appeal packet endpoints, `coverage` (how complete the newest months are and which payer weeks are missing) and `activity/overview|trend|recent|provider/{id}` (what each clinician did). `static/index.html`, `static/features.js` and `static/providers.js` are a single-page dashboard (vanilla JS, Chart.js from a CDN, no build step).

Authentication is one shared HTTP Basic login. An optional second password (`REMITWATCH_OWNER_PASSWORD`) keeps what each clinician is doing for the owner: the page sends it in the `X-Owner-Password` header with the `activity` calls, `breakdown` and the rate calls, and holds it in memory only (forgotten on Lock, on leaving the page and after half an hour). Without it the `activity` routes answer 403 (never 401, which would make the browser drop its login), and `breakdown`, `rates` and `rates/matrix` leave out the number of lines wherever three quarters or more of a cell is one clinician's work, which covers the provider dimension and any stand-in for it. Wrong tries are slowed by their own counter. It is a screen for the dashboard, not encryption: the state folder holds the same tables. State-changing calls must carry an `X-Requested-With` header, which a cross-site form cannot send. With `REMITWATCH_SHOW_PHI=0` the API returns an allowlist of claim columns and the patient's initials (none in the CSV export); names and member numbers appear only in the packet PDF, which every login can build, and the search box does not look at names or run any query on them (it would otherwise answer whether a given person is a patient). With `REMITWATCH_SHOW_PHI=1` every findings list, the detail view and the CSV export carry the name and member number from the remittance, and the search box matches them. In both settings the detail view sends the same allowlist of claim columns, never the whole claim loop. A number is treated as a member number only when the payer labelled it one: a Social Security number (qualifier 34), the old Medicare claim number built on one (HN) and a tax ID (FI) are never shown, exported or searched. Clicking a name asks for the patient on that claim (`patient_of`, same name and same member number exactly), because one member number often covers a family. The page sends the search text in an `X-Search` header and fetches the export, so a name is never part of an address that an access log keeps. The patient is attached to a page of findings by primary key, so it needs no other index. One known limit when names are hidden: the reference a payer writes on an overpayment recovery is shown as the payer wrote it; in this practice's files it holds claim numbers, but a payer could put a member number there. Every value that originates in a payer or EMA file is escaped before it is put in the page.

## Scheduling

`pipeline.py` owns the cycle and its lock. The lock lives in the store, carries a token and a heartbeat, can only be released by the cycle that took it, and is taken over after three hours without a heartbeat. The dashboard process runs a scheduler thread that starts a cycle on the configured interval, when Dropbox calls the webhook (after a short wait so a batch of uploads finishes), and when someone presses "Sync and analyse now". Each cycle runs in a child process so that the memory detection needs is returned afterwards and a failure cannot take the dashboard down.

`dropbox_sync.py` lists the folder with `files/list_folder` (recursive) and stores the cursor; later runs call `list_folder/continue` so only new or changed files are downloaded. A refresh token obtained once with `remitwatch.cli oauth` keeps it unattended.

## Serverless mode (Vercel)

`runtime.serverless()` is true when the `VERCEL` variable is present. Nothing on disk survives there and no process outlives a request, so `cloud.py` replaces `pipeline.py`:

- The store is a compact SQLite image (no secondary indexes) held in memory for one run with `deserialize` and written back with `serialize`, gzip'd, to a state folder in the practice's Dropbox (`state.py`). It is started from a serialized image even when empty, so SQLite keeps it in one block and saving needs one copy, not two.
- A run takes a lock file (created with Dropbox's add-only write, so two runs cannot both get it), folds the journal, ingests until half its time is gone, detects, alerts, saves the store, then the dashboard's copy built from it, then writes the Dropbox position, then sends mail. The store goes first because it is the record: a version number in the store and in `serving.json` shows when the dashboard's copy was left behind, and the next run rebuilds it. The position lives in its own small file and is written after the store, so it never runs ahead of it and an idle run uploads nothing large. What was mailed is kept in `notified.json` for the same reason.
- When there is more to do the run calls its own `/api/cron/cycle` with the `CRON_SECRET` and returns. Detection is handed to a fresh run after a large batch. A run that made no progress does not call itself again, a chain ends after 30 runs whatever happens, and a notification from Dropbox never restarts a run that failed in the past six hours or died.
- Only a download that might work next time holds the Dropbox position back. A file that cannot be read is recorded in the store (so it is not fetched again until it changes) and listed on the Data tab.
- A journal entry is deleted when the saved store holds its effect. One that names a finding the store does not hold is kept and tried again after each detection, for a week.
- The dashboard reads a smaller database built by each run: findings, rates, exposure, alerts and the claim detail behind each finding. Every instance keeps a local copy under a new file name per version and overlays the journal (`actions/*.json`), so a status change shows at once and reaches the store on the next run. Replaying a journal entry is harmless.
- The first load reads `Trizetto.zip` in one download when the data folder has it, and recognises the same files met later as loose copies by their Dropbox content hash.

Measured on the archive (almost three years of remittances): first load in two runs (54 and 36 seconds on a 2.1 GHz core), 1.43 GB peak; a routine re-analysis peaks at about 1.5 GB. Results match the disk-based store's: the same rates and alerts, and findings within two of 17,400 and about a hundred dollars of $3.7M. The difference is eight pairs of duplicate remittances from late 2024 of which a different copy survives depending on the order the files are read in.

Rules that keep the two modes equal: a file keeps its own name and date when it is ingested, the most common rate of a group does not depend on the order files were read in (`usual_amount`), and when a payer sends the same payment twice the copy from the newer file wins.

## Limits and next steps

- Expected amounts are revealed-rate baselines, not contracts. Loading contracts would turn "below usual" into "below contract".
- Filing windows come from provider manuals where they were read and from payer type otherwise; a contract can differ.
- Location and diagnosis are known only where EMA exports exist (late July 2026 onward); location is otherwise estimated from the provider's usual site.
- The store is one SQLite file (about 0.9 GB for 32K remittances, 0.5 GB in the compact form). Detection holds every line in memory: a run peaks at about 1.5 GB, which fits a 2 GB serverless function today and grows with the archive.
- A store held in memory from a serialized image cannot pass 1 GB (SQLite's default limit for it).
- The visit-note review has not been exercised against the live API in this build.
