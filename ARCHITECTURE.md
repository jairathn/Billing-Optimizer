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

`ema_exports.py` maps every known ModMed EMA export layout (annual and weekly, 12 to 42 columns) to one canonical row shape. The Claim ID in EMA (for example `CB00EAJD9C017`) is the 835 CLP01, which is how remits join to the practice management system. REF 6R in the 835 is not the EMA Bill Item Identifier; line matching uses claim id, date of service, code, modifiers, units and charge.

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

## Serving

`api.py` exposes JSON endpoints: summary, findings with filters and CSV export, finding detail, payer rates, `breakdown` (any one or two of 23 dimensions with denominators), `progress`, `alerts`, `sync/status`, `sync/run`, the Dropbox webhook, and the appeal packet endpoints. `static/index.html` and `static/features.js` are a single-page dashboard (vanilla JS, Chart.js from a CDN, no build step).

Authentication is one shared HTTP Basic login. State-changing calls must carry an `X-Requested-With` header, which a cross-site form cannot send. With `REMITWATCH_SHOW_PHI=0` the API returns an allowlist of claim columns and the patient's initials; names and member numbers appear only in the packet PDF. Every value that originates in a payer or EMA file is escaped before it is put in the page.

## Scheduling

`pipeline.py` owns the cycle and its lock. The lock lives in the store, carries a token and a heartbeat, can only be released by the cycle that took it, and is taken over after three hours without a heartbeat. The dashboard process runs a scheduler thread that starts a cycle on the configured interval, when Dropbox calls the webhook (after a short wait so a batch of uploads finishes), and when someone presses "Sync and analyse now". Each cycle runs in a child process so that the memory detection needs is returned afterwards and a failure cannot take the dashboard down.

`dropbox_sync.py` lists the folder with `files/list_folder` (recursive) and stores the cursor; later runs call `list_folder/continue` so only new or changed files are downloaded. A refresh token obtained once with `remitwatch.cli oauth` keeps it unattended.

## Serverless mode (Vercel)

`runtime.serverless()` is true when the `VERCEL` variable is present. Nothing on disk survives there and no process outlives a request, so `cloud.py` replaces `pipeline.py`:

- The store is a compact SQLite image (no secondary indexes) held in memory for one run with `deserialize` and written back with `serialize`, gzip'd, to a state folder in the practice's Dropbox (`state.py`). It is started from a serialized image even when empty, so SQLite keeps it in one block and saving needs one copy, not two.
- A run takes a lock file (created with Dropbox's add-only write, so two runs cannot both get it), folds the journal, ingests until half its time is gone, detects, alerts, saves the dashboard's copy and the store, then writes the Dropbox position. The position is written last and lives in its own small file, so it never runs ahead of the saved store and an idle run uploads nothing large.
- When there is more to do the run calls its own `/api/cron/cycle` with the `CRON_SECRET` and returns. Detection is handed to a fresh run after a large batch. A run that made no progress does not call itself again.
- The dashboard reads a smaller database built by each run: findings, rates, exposure, alerts and the claim detail behind each finding. Every instance keeps a local copy under a new file name per version and overlays the journal (`actions/*.json`), so a status change shows at once and reaches the store on the next run. Replaying a journal entry is harmless.
- The first load reads `Trizetto.zip` in one download when the data folder has it, and recognises the same files met later as loose copies by their Dropbox content hash.

Measured on the five-year archive: first load in two runs (77 and 38 seconds on a 2.1 GHz core), 1.42 GB peak. Results equal the disk-based store's: same findings count, same dollars, same rates and alerts.

Rules that keep the two modes equal: a file keeps its own name and date when it is ingested, the most common rate of a group does not depend on the order files were read in (`usual_amount`), and when a payer sends the same payment twice the copy from the newer file wins.

## Limits and next steps

- Expected amounts are revealed-rate baselines, not contracts. Loading contracts would turn "below usual" into "below contract".
- Filing windows come from provider manuals where they were read and from payer type otherwise; a contract can differ.
- Location and diagnosis are known only where EMA exports exist (late July 2026 onward); location is otherwise estimated from the provider's usual site.
- The store is one SQLite file (about 0.9 GB for 32K remittances, 0.5 GB in the compact form). Detection holds every line in memory: a run peaks at about 1.4 GB, which fits a 2 GB serverless function today and grows with the archive.
- A store held in memory from a serialized image cannot pass 1 GB (SQLite's default limit for it).
- The visit-note review has not been exercised against the live API in this build.
