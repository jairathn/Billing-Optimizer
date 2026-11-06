# Architecture

## Pipeline

```
Dropbox "/Trizetto Data"                    local mirror                 SQLite store                 dashboard / report
  32K+ .RMT (X12 835)  ──list_folder──▶  data/dropbox/...  ──parse──▶  transactions, claims, lines,  ──detect──▶  findings ──▶  API + UI
  EMA xlsx exports        (cursor delta)                      load       adjustments, remarks, plb,                 payer_rates     weekly email
  837 claim files (.txt)                                      read       ema_rows, claims_837, lines_837            fee_table       appeal packets
```

One process, one SQLite file. The weekly job (`remitwatch.cli weekly`) runs the four steps in order and is safe to re-run: files are skipped by content hash, 835 transactions by content fingerprint, EMA rows by row fingerprint, and findings keep their status across runs.

## Parsing

`x12_835.py` reads 005010X221A1 835s: ISA/GS envelopes (several per file are handled), BPR/TRN payment identity, 1000A/B payer and payee, 2000 LX, 2100 CLP with NM1/REF/DTM/AMT/MOA/MIA, 2110 SVC with DTM/CAS/REF/AMT/QTY/LQ, and PLB. Money is integer cents. Each line keeps the adjudicated code (SVC01), the submitted code when reported (SVC06), modifiers, charge, paid, allowed (AMT B6), units, service date, the line item control number (REF 6R), and all CAS adjustments and RARCs. Balancing is checked at line, claim and transaction level. A content fingerprint of ST..SE lets the store ignore the same remittance delivered under a new file name, and `mark_superseded` flags older versions of a trace number that was re-delivered with different content.

`x12_837.py` reads 837P claim files (CLM, SV1, DTP 472, REF 6R) so paid lines can be compared with submitted lines even when the payer omits SVC06.

`ema_exports.py` maps every known ModMed EMA export layout (annual and weekly, 12 to 42 columns) to one canonical row shape. The Claim ID in EMA (for example `CB00EAJD9C017`) is the 835 CLP01, which is how remits join to the practice management system. REF 6R in the 835 is not the EMA Bill Item Identifier; line matching uses claim id, date of service, code, modifiers, units and charge.

## Baselines (no contracts needed)

- Provider tier: each rendering NPI is classified physician or non-physician from its Medicare allowed amounts (non-physician practitioners are paid 85 percent).
- Fee table: the practice's charge amounts per code and year, learned from unaltered lines. A charge that equals the fee of a higher-level code reveals a downcode even when the payer hides the original code.
- Payer rates: per payer group, year, code and tier, the most common allowed amount on cleanly paid primary lines, using only the highest-ranked surgical procedure on each claim so multiple-procedure reductions (which many payers apply without CARC 59) do not pollute the mode. Pricing modifiers (26, TC, 50, 52, 80...) are excluded.
- Medicare baseline: the same rates from Medicare KY (CGS J15), falling back to Medicare IN, per year, code and tier.
- Expected amount for a line: the payer's usual rate, halved for the second surgical procedure (quartered for later ones on commercial plans), as a rank-aware expectation.

## Detection

`detect.py` loads all non-superseded lines with their claim and transaction context, builds the baselines, then runs line rules (downcoding, zero-paid lines inside paid claims, underpayment), claim rules (denied claims never paid later) and aggregate rules (fees, takebacks, G2211, MIPS, sequestration, contract rate gaps). Each finding gets a stable id from the signal and the line or claim key, a severity, an action, the expected amount and shortfall, the CARC/RARC with text, an evidence JSON, and an estimated appeal deadline (payment date plus 120 days Medicare, 60 MA, 180 commercial, 365 UHC family; refine from contracts).

Payer names from the 835 are normalized to payer groups and kinds (medicare, medicare_advantage, medicaid, commercial, supplement, tricare, va); the claim filing indicator (CLP06 = 16) marks Medicare Advantage products that share a commercial payer name.

## Serving

`api.py` exposes JSON endpoints (summary, findings with filters and CSV export, finding detail with claim lines, adjustments, remarks, remit header, claim history and EMA rows, payer rates and a rates matrix, appeal packet, status updates). `static/index.html` is a single-page dashboard (vanilla JS, Chart.js from a CDN). Authentication is a single shared HTTP Basic login; patient names are reduced to initials unless `REMITWATCH_SHOW_PHI=1`.

## Scheduling

`dropbox_sync.py` lists the folder with `files/list_folder` (recursive) and stores the cursor; later runs call `list_folder/continue` so only new or changed files are downloaded (`files/download` by id). A refresh token obtained once with `remitwatch.cli oauth` keeps the job unattended. `report.py` writes a Markdown summary (new findings, appeals due in 45 days, correctable errors) to `data/reports/` and emails it when SMTP is configured.

## Limits and next steps

- Expected amounts are revealed-rate baselines, not contracts. Loading contracts (fee schedules with effective dates) would turn "below usual" into "below contract".
- Appeal deadlines are defaults by payer type.
- Line matching to EMA uses composite keys; the annual EMA exports are not yet in Dropbox, so EMA context exists only from late July 2026.
- The store is one SQLite file (about 0.7 GB for 32K remits); it is fine for one practice and one small host.
