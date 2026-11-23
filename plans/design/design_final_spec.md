# RemitWatch and EMA's A/R and Production reports: merged, buildable spec

**Base:** `main` at b3c05a5 (rules 2.6).

**Where the figures come from:**
- Every figure is an aggregate measured on the practice's real files: postings from Jan 2025 to Nov 20, 2026, and 835s up to 2026-10-09.
- No figure, cell value, practice-specific payer alias or EMA tenant hostname from those files may go into code, tests or docs.
- Tests use invented rows built from the column layout only.
- Two measurements are new in this review, both aggregate-only: how many claim lines the store must keep, and how long EMA takes to post an 835. The script is `/tmp/claude-0/-home-user-Billing-Optimizer/96e81489-2ad2-5c70-991d-e00db2e22749/scratchpad/arcoll/judge/j1_lean.py`, run with `python3 -I -B`.

## 0. Verdict on the three designs

| Criterion (1-10) | 1 Pipeline | 2 Money | 3 Workflow |
|---|---|---|---|
| Money found and risk caught | 3 | 9 | 8 |
| Correctness (false positives, double counting with existing findings) | 9 | 8 | 6 |
| Feasible on both the AWS server and Vercel | 8 | 6 | 7 |
| PHI and public-repo safety | 8 | 9 | 6 |
| Biller time saved | 4 | 7 | 9 |
| **Total (of 50)** | **32** | **39** | **36** |

### Design 1, Pipeline (32)

**Strengths.** The best intake, and the only design that reads the 160 MB and 206 MB files on Vercel.
- Recognises each report by its exact headers, before today's family test runs.
- Streams the files: a 520K-row file reads in 33 MB.
- Keeps a posting ledger outside the in-memory store.
- Gives an invariant that the newest file wins per service date, including when files arrive late.
- Never de-duplicates by content. The 487 identical postings in the files are real.
- Builds a claim-link map that records conflicts, and marks which EMA payments an 835 carries.
- Repairs itself after a crash on Vercel.

**Weaknesses.**
- It specifies no check, so on its own it finds no money and saves no biller time.
- It refuses exports filtered by posted date.
- It keeps all 204K claim lines and 281K payments in the store (46 MB).
- It stores the MRN in clear.
- It raises the upload limit to 450 MB, which is not needed.

### Design 2, Money (39)

**Strengths.** The best accounting of the money.
- Twelve checks, ranked, with each dollar counted under one check. Its split of the contractual-labelled denials shows that check adds about $0 of new money.
- Build 1 takes the larger of two write-off figures instead of adding them.
- ASC twin claims are kept out of closures and causes.
- Work is handed over when the 835 arrives.
- It stores keys only, never names or other PHI.
- It found a real rules bug, verified in this review. `normalize_payer("VA CCN OPTUM")` returns Optum (UHC), commercial, with a 365-day appeal and 90-day filing window, instead of VA Community Care (90 and 180 days). `WPS-VACAA` falls to an unknown group.

**Weaknesses.**
- Aggregated tables cannot replace a posted-date window or show individual postings.
- Vercel refuses both backfill files, so that site has no history unless the practice re-exports by quarter.
- C1 and C4 can count the same mis-keyed check twice.
- Deadlines for EMA-only items start at EMA's posting date. EMA posts a median 3 days after the payer's 835 date (p95 8 days, measured here).

### Design 3, Workflow (36)

**Strengths.** The best experience for the biller.
- One worklist, with an Open in EMA button.
- An exact-word dictionary of EMA statuses. It turns 43 "Batched for Printing" chases into claims that were never sent.
- "Worked in EMA" read from what changes between A/R pulls.
- An "unworked" number, and an A/R panel whose buckets add up to the total.
- A fax hold decided per item, which unblocks appeals while the 835s are out.
- Lean summaries in the store.

**Weaknesses.**
- EMA-only denials also fire on 835 lines that RemitWatch chose not to flag (OA-209, PR-49, CO-246). That contradicts detect.py.
- A data-driven pattern skip could hide real denials from the outage.
- Its scope test compares spans, and ties on the very files the practice sent.
- It swaps Production's date into every clock.
- It stores the text of billing notes.
- It refuses both backfill files on Vercel.

### What the merge takes

| From | What |
|---|---|
| Design 1 | Recognition, the streaming readers, the ledger and its box invariant, the claim-link map, the in-835 marks, the guard step and the Vercel streaming path |
| Design 2 | The checks, "the 835 decides", one dollar per check, the PHI rule, the VA fix and the release gate |
| Design 3 | Lean summaries, posted-date pulls, Open in EMA, the status dictionary, NOT_SENT, worked in EMA, the unworked number, the A/R panel and the per-item fax hold |

New in this review:
- A scope test that cannot tie, plus a hold for files filtered by both dates.
- A `misnumbered` cause that pairs C1 with C4.
- EMA-only deadlines counted from the posting date minus the measured posting lag.
- The A/R used as Build 2's clock during the outage, so the A/R checks ship before Production.
- A measured lean store.

## 1. Facts this spec rests on

1. **Today's code misreads both reports.**
   - `ema_exports.detect_family` returns `claim_status` for any header holding Claim Status or Denial Amount.
   - So the A/R .xlsx files in "Trizetto Data/Trizetto/AR and Collections Reports from EMA" are read by both hosts as Claim & Claim Status rows with `claim_id` NULL (9,653 rows).
   - Their hashes in `files` then stop a correct re-read.
   - They feed no rule, because every rule needs `claim_id IS NOT NULL`, but they inflate the EMA counts.
2. **`.csv` is not in `dropbox_sync.WANTED_EXT`.** Adding it alone would break both hosts:
   - The server would read a 206 MB CSV whole: `read_export_detail` builds every row, about 1.3 GB for 520K rows.
   - Vercel would mark both CSVs "(too large)" in `dropbox_seen` and never look at them again.
   - The Upload page already accepts both reports as Claim & Claim Status and reads them whole in the web process.
3. **Production report.**
   - 39 columns, one row per posting.
   - 2026: 408,599 rows (160 MB). 2025: 520,326 rows (206 MB). Each file is cut by service year.
   - 204,359 claim lines in all.
   - No claim id: the Claim Link is EMA's bill number.
   - Adjustment rows carry no check number. Patient-side rows carry no link.
   - The files are 12-15 MB zipped. The money identities hold on 100% of rows.
4. **A/R report.**
   - 22 columns. 9,411 rows for 2026 plus 242 for 2025.
   - The Aging Date resets every cycle (all 7,343 pending rows fall within 30 days of it), so it is never a clock.
   - Patient MRN is not EMA's Patient ID.
5. **Joins.**
   - The Claim Link maps one-to-one to an 835 bill for 82,272 of 84,032 bills with 2025+ service (98%). Of these, 68,361 map by check, date, code and patient, and 13,917 by patient, date and code.
   - EMA's posted payment equals the 835 line on 99.6% of 164,651 lines. Its allowed amount equals the 835's on 84.2%.
6. **New measurement: lean store.** The lean keep rule (7.1) keeps 74,554 of 204,359 linked claim lines (36.5%).
7. **New measurement: posting lag.** EMA posts an 835 payment a median 3 days after its payment date (p90 6, p95 8, p99 14 days), over 16,925 matched checks. 2.1% are posted earlier.
8. **Measured costs.**
   - Streaming read: 11.5 s and 9.2 s for the two files, at 33 MB of memory.
   - Ledger with two indexes: 124 MB, or 29.7 MB at gzip level 1 (1 s).
   - Full summary tables: 2.0 s, 46 MB.
   - An .xlsx with the same layout streams at about 2.5 minutes per 520K rows.
9. **Vercel today.**
   - The whole store is held in memory: compact, about 0.5 GB, and an image cannot pass 1 GB.
   - Peak memory 1.52 GB of 2 GB. Budget 240 s. Files over 60 MB are marked seen and skipped.
   - `DropboxClient.download_chunks`, `DropboxState.get_chunks`/`put_chunks` and `scripts/cloud_bench.py` already exist.

## 2. Build order

Each step is one PR with its own tests and docs, and can be deployed alone. The switch `REMITWATCH_EMA_REPORTS` (6.9) turns report reading off on any host.

**Until Step 0 is live, tell the practice not to put the Production CSV on the Upload page.** Dropbox is safe until then, because .csv files are not read.

| Step | What ships | Modes | Rules version | Acceptance on the real files (section 15) |
|---|---|---|---|---|
| 0 | Guard: recognition, reports kept as `waiting`, misread rows purged | both | — | the four files recognised; 0 report rows in `ema_rows` |
| 1 | A/R in: reader, snapshots, history, catalog, Open in EMA, claim view, Data tab | both | — | 9,653 rows; 4,229 + 61 claim ids |
| 2 | A/R checks: status dictionary, NOT_SENT, Build 2 from the A/R, VA fix, credits and patient A/R reports, A/R panel v1 | both | 2.7 | C3, C8 figures |
| 3 | Production on the server: ledger, scopes, guards, decisions, summaries, map, claim view. Vercel records Production as waiting | server (Vercel waiting) | — | map ≥ 95%; about 75K lines kept |
| 4 | Production on Vercel: streaming, ledger in the state folder, gate, switch | Vercel | — | gate in 8.3 |
| 5 | Precision: C6 closures, C2 Build 1 v2, C3 answers from EMA and the Production clock, W5 per-item fax hold | both | 2.8 | about 259 closures; about 900 marks |
| 6 | Money visibility: C1 missing remittances, C4 unposted payments | both | 2.9 | $2.74M ± 2% |
| 7 | EMA-only denials C5 with hand-over, C7 lever, C9 and C10 reports | both | 2.10 | about 460 lines inside the window |
| 8 | Workflow: worked in EMA, unworked number, A/R panel v2, chips, Today, Progress, weekly email | both | 2.11 | A/R buckets equal the A/R total |
| 9 | Later: inbox folder and machine upload, ASC facility-fee audit, slimming the weekly routine | server | — | — |

## 3. Files

### New
- `remitwatch/ema_reports.py`: recognition, readers, keys, A/R snapshots, ingest, preview, migration, the switch, the claim URL prefix, and the store DDL with `ensure_schema`.
- `remitwatch/ema_ledger.py`:
  - the posting ledger (`Ledger`; `LedgerHandle` for Vercel);
  - applying files, retention;
  - `refresh()` (Part A and Part B), the claim-link map, coverage.
- `remitwatch/ema_checks.py`: `status_class`, turnaround and posting lag, C1-C11, W2-W4, `reconcile_summary`.
- `tests/ema_fixtures.py`, `tests/test_ema_reports.py`, `tests/test_ema_ledger.py`, `tests/test_ema_checks.py`, `tests/test_ema_vercel.py`.

### Changed
- `ema_exports.py`: `detect_family` tests the two reports first. `iter_export_paths` yields a .csv only when it is a report.
- `uploads.py` and `static/upload.html`.
- `dropbox_sync.py`: `WANTED_EXT`, `wanted()`, the router.
- `cloud.py`: big files, the ledger handle, save order, `SERVING_TABLES`, `SERVING_BUILD` "7", the meta filter, `_ingest_batch` origins.
- `pipeline.py`.
- `codes.py`: VA rule, aliases.
- `detect.py`, `insights.py`, `alerts.py`, `actions.py`, `api.py`, `report.py`, `cli.py`.
- `static/today.js`, `desk.js`, `features.js`, `claim.js`, `index.html`.
- `.env.example` and the docs in section 13.

`uploads.MAX_BYTES` stays at 250 MB, and Caddy at 260 MB. Both CSVs fit, and zipped they are 12-15 MB.

## 4. Recognition and readers (`ema_reports.py`)

### 4.1 Recognition
- `norm(h)`: strip, lower-case, collapse spaces, and remove spaces around "-" and "/". For example, "Non- Contractual Adjustments" becomes "non-contractual adjustments".
- **Production core:** posted date, service date, cpt/product, check number, claim link, insurance payments, patient payments, contractual adjustments, and no activity type column.
- **A/R core:** a/r claim id, responsible party, a/r balance type, current a/r ($) (aging), service date.
- **Finding the header:**
  - The header is the first of the first 25 rows (`HEADER_SCAN`) that holds every core name.
  - Production is tried first, then the A/R, then the three existing families exactly as today.
  - `detect_family` tests the two cores first and returns `production` or `ar_report`, so no caller can misread them again (`uploads._export_text`, `iter_rows`).
- **Columns.** A missing required column refuses the file, naming the column and why it is needed. Ignored columns are never read.

| Report | Required | Optional | Never read |
|---|---|---|---|
| Production | core + Adjustment Reason Code, Payer, Charges, Non- Contractual Adjustments, Patient Adjustments, Patient Name, Patient DOB | Units, Claim Status, Claim Action, Payer Claim Control Number, Payer Plan Name, Primary Payer, Secondary Payer, Payment Allowed Amount, Unit Allowable, Insurance Charges, Payments, Adjustments, Insurance Adjustments (the last three only to check the money identities) | CPT/Product Description (Custom), Procedure Count, Primary Biller, Primary Provider, Total wRVU, Attendee Name, Bill Assignee, Claim Assignee, E/M Only Claim?, Payment Follow-Up Date, Local Tax |
| A/R | core + Coverage Type, Claim Link, Claim Status, CPT/Product, Primary Payer, Patient Name | Aging Date, Denial Amount, Bill Follow Up Date, Claim Action, Denial Category, Last Billing Note, Secondary Payer, Patient MRN | CPT/Product Description, Primary Biller, Primary Provider |

- **`recognize(path_or_fileobj, name) -> Recognized | None`** reads:
  - for text, at most 64 KB. The delimiter is whichever of comma, tab, pipe or semicolon splits the header into the most known names;
  - for .xlsx, the first 25 rows of the first matching sheet (openpyxl `read_only`), plus the sheet's dimension;
  - for a zip, each member's first 64 KB. Members ending .csv, .tsv, .txt or .xlsx count; `__MACOSX` and hidden members are skipped; a zip inside a zip is refused.
- `Recognized` fields: report, fmt, header_index, index (canonical name -> position), missing, unused, delimiter, dimension.

### 4.2 Streaming input
- **Text:** binary lines decoded as utf-8 (BOM stripped). A line that fails to decode falls back to cp1252 for that line alone. `csv.reader` runs over the decoded lines.
- **One reader for every source:** a disk file, a zip member (`ZipFile.open`) and a Dropbox stream (`download_chunks` wrapped as `io.RawIOBase`) are all read the same way.
- **xlsx:** openpyxl `read_only`, `values_only`.
- No reader ever holds a list of rows.

### 4.3 Cells
- **Placeholders** become NULL (`ema_exports.NA_TEXT`).
- **`money(v)` -> cents.**
  - A number becomes round(v*100).
  - Text must match `^(-)?\(?(-)?\$?(-)?(\d{1,3}(,\d{3})*|\d+)(\.(\d*))?\)?$`. Parentheses or a minus sign make it negative; "$45." becomes 4500.
  - Anything else becomes NULL and is counted in `bad_amounts`.
- **`date(v, order)`.**
  - Accepts date cells, M/D/YYYY (a two-digit year becomes 20YY), and YYYY-MM-DD with an optional time.
  - Excel serial numbers 20000-80000 are accepted in a date column.
  - The order is decided per file from the first 5,000 values. A first part above 12 with no second part above 12 means day-first. Both happening refuses the file ("dates in two orders").
- **`link(v)`:** match `/claim/(\d{1,12})/?\s*$` and keep the int. The URL is dropped at once.
- **`check(v)`:** stripped text, with leading zeros kept. A value matching `^\d(\.\d+)?E[+-]?\d+$` is kept but gets flag 4 (damaged by a spreadsheet) and is counted.
- **`code(v)`:** upper-cased. It is CPT/HCPCS when it matches `^(\d{4}[0-9A-Z]|[A-V]\d{4})$`; otherwise it is a product (flag 1).
- **`reason(v)`:** `^(CO|PR|OA|PI|CR)\s*-\s*([A-Z0-9]{1,4})$` gives the group and code. Any other text is an EMA adjustment kind, kept by name with the "Adjustment - " prefix dropped.
- **Names become tokens:**
  - the `[A-Z]{2,}` words of the last and first name;
  - the suffixes JR, SR, II, III and IV dropped;
  - the rest sorted. This is the rule the sizing measured at 98%.
  - Names, DOB and MRN become keys (5.2) inside the reader and go no further.

### 4.4 Rows
**Production.**
- A row is kept when its service date and posted date parse.
- Other rows are counted as blank, total (`TOTAL_LINE`) or no_date.
- When Payments, Adjustments and Insurance Adjustments are present, the three money identities are checked. A break sets flag 8 and is counted.
- Flags: 2 = posted before service; 16 = patient side (Payer = "Patient").

**A/R.** A row is kept when it has a service date and a Responsible Party.

**`account`** holds counts and dates only:
- rows_read, rows_kept, left by kind;
- bad_amounts, bad_dates, damaged_checks, identity_breaks;
- hosts (a count only);
- svc_lo/hi, posted_lo/hi, links, payers, date_order;
- charge_line_share: the share of claim lines with a row whose Charges > 0.

### 4.5 The claim address (Open in EMA)
- **Collecting.** While reading, the prefix of each Claim Link up to "/claim/" is collected. At most 3 are kept, and they are never logged.
- **Storing.** Exactly one prefix matching `^https://[a-z0-9][a-z0-9-]{0,62}\.ema\.md/ema/ws/v\d{1,3}/qlikRedirect/claim/$` (case-insensitive) is stored in store meta `ema:claim_url_prefix`. The pattern names the vendor's public domain, never a tenant label.
- **Two prefixes** refuse the file ("claims from two EMA systems").
- **Override.** `REMITWATCH_EMA_CLAIM_URL` (a template containing `{claim}`, or `off`) overrides the learned prefix.
- **In the browser:**
  - a link is built only for numbers matching `^\d{6,12}$`;
  - it opens with `target=_blank rel="noopener noreferrer" referrerpolicy="no-referrer"`;
  - no RemitWatch address ever carries the number.

## 5. Storage

### 5.1 Never stored
- Patient Name, DOB and MRN (keys only).
- Primary Biller, Primary Provider, Attendee Name, Bill Assignee, Claim Assignee.
- Descriptions.
- The Last Billing Note text. Only has_note, note_chars and a keyed hash are kept.
- The Claim Link URL and host, except the validated prefix in 4.5.
- Total wRVU, Procedure Count, Local Tax, Payment Follow-Up Date, E/M Only Claim?.

The PHI that is stored is of kinds the store already holds: claim ids, link numbers, check numbers, dates, codes, amounts, and the A/R's patient initials. Note text and the MRN stay out unless the owner decides otherwise (**[Owner decisions O5, O6]**).

### 5.2 Keys
- **Salt.** Store meta `ema:key_salt` holds 32 random bytes as hex. It is made on the first read and never copied to the dashboard's copy: the meta filter adds `key NOT LIKE 'ema:key_salt%'`.
- **Key functions:**
  - `name_key` = the int64 of the first 8 bytes of HMAC-SHA256(salt, "N|" + tokens).
  - `person_key` adds "|" + DOB.
  - `mrn_key` = HMAC(salt, "M|" + MRN).
  - `note_hash` = HMAC(salt, "T|" + note).
- **835 side.** The SQL function `rw_name_key(last, first)`, registered on the store connection, gives the 835 side the same key. It is also applied to the corrected name when one is present.
- **Ledger check.** The ledger records `salt_fp` = sha256(salt)[:16]. A mismatch empties the ledger and re-reads its files (catalog status `reread`).

### 5.3 The posting ledger (a separate SQLite file, `ema_ledger.py`)
**Location.**
- **Server:** `REMITWATCH_EMA_LEDGER`, or `<store folder>/ema_ledger.sqlite` (mode 0600, WAL).
- **Vercel:** `ema_ledger.sqlite.gz` in the state folder.
  - Only a run that applies a Production file unpacks it, to `<tmp>/remitwatch/ema_ledger.sqlite`, with `journal_mode=OFF` and `synchronous=OFF`.
  - The copy in the state folder is the durable one. A failed run throws its scratch copy away.

```sql
CREATE TABLE ledger_meta (key TEXT PRIMARY KEY, value TEXT);          -- schema '1', seq, salt_fp
CREATE TABLE ledger_files (src INTEGER PRIMARY KEY, file_id TEXT UNIQUE, scope TEXT,
    svc_lo TEXT, svc_hi TEXT, posted_lo TEXT, posted_hi TEXT, as_of TEXT, created_at TEXT, received_at TEXT,
    rows INTEGER, live_rows INTEGER, status TEXT);
CREATE TABLE names (id INTEGER PRIMARY KEY, name TEXT UNIQUE);         -- payers, plans, statuses, actions, EMA kinds
CREATE TABLE postings (src INTEGER NOT NULL, posted TEXT NOT NULL, svc TEXT NOT NULL, cpt TEXT, units REAL,
    link INTEGER, check_no TEXT, grp TEXT, code TEXT, kind INTEGER, status INTEGER, action INTEGER,
    payer INTEGER, plan INTEGER, primary_payer INTEGER, secondary_payer INTEGER, pccn TEXT,
    person INTEGER, name_key INTEGER, charges INTEGER, ins_charges INTEGER, ins_paid INTEGER, pat_paid INTEGER,
    contractual INTEGER, noncontractual INTEGER, pat_adj INTEGER, allowed INTEGER, unit_allowable INTEGER, flags INTEGER);
CREATE INDEX ix_p_svc ON postings(svc);
CREATE INDEX ix_p_posted ON postings(posted);
CREATE INDEX ix_p_link ON postings(link) WHERE link IS NOT NULL;
```

- **Size:** about 140 MB for two years with three indexes, about 33 MB gzipped. This is estimated from the measured 124 MB and 29.7 MB with two indexes.
- **`Ledger`** methods: `open`, `apply_production(file, rows, force)`, `files()`, `seq()`, `retain(cutoff)`, `salt_fp()`, `close()`.

### 5.4 Store tables
They are defined in `ema_reports.DDL`. `ensure_schema` is called from `Store.__init__` and from `detect.ensure_schema`.

```sql
CREATE TABLE IF NOT EXISTS ema_report_files (file_id TEXT PRIMARY KEY, report TEXT, source_name TEXT, via TEXT,
  origin TEXT, container TEXT, format TEXT, scope TEXT, svc_lo TEXT, svc_hi TEXT, posted_lo TEXT, posted_hi TEXT,
  as_of TEXT, created_at TEXT, received_at TEXT, applied_at TEXT, rows_read INTEGER, rows_kept INTEGER,
  rows_applied INTEGER, live_rows INTEGER, links INTEGER, account TEXT, columns TEXT, missing TEXT, unused TEXT,
  status TEXT, status_reason TEXT, attempts INTEGER DEFAULT 0, replaced_by TEXT, decision TEXT, decided_by TEXT, decided_at TEXT);
  -- status: waiting | current | partly_replaced | replaced | superseded | held | refused | discarded | reread
  -- origin (never served): server path or upload id; Vercel Dropbox id, path, content hash, size
CREATE TABLE IF NOT EXISTS ema_ar (file_id TEXT, row_no INTEGER, as_of TEXT, svc_year TEXT, item_key TEXT,
  coverage TEXT, party TEXT, claim_id TEXT, bill TEXT, link INTEGER, balance_type TEXT, balance_cents INTEGER,
  denial_cents INTEGER, aging_date TEXT, follow_up TEXT, claim_action TEXT, claim_status TEXT, status_class TEXT,
  cpt TEXT, product INTEGER, denial_category TEXT, has_note INTEGER, note_chars INTEGER, note_hash INTEGER,
  primary_payer TEXT, secondary_payer TEXT, payer_group TEXT, payer_kind TEXT, svc TEXT, initials TEXT,
  name_key INTEGER, mrn_key INTEGER, PRIMARY KEY (file_id, row_no));
CREATE INDEX IF NOT EXISTS ix_ar_claim ON ema_ar(claim_id);
CREATE INDEX IF NOT EXISTS ix_ar_link ON ema_ar(link);
CREATE TABLE IF NOT EXISTS ema_ar_items (item_key TEXT PRIMARY KEY, party TEXT, coverage TEXT, claim_id TEXT,
  link INTEGER, cpt TEXT, svc TEXT, first_seen TEXT, last_seen TEXT, snapshots INTEGER, gone_since TEXT,
  first_status TEXT, last_status TEXT, last_balance_cents INTEGER, max_balance_cents INTEGER, last_action TEXT,
  last_note_hash INTEGER, note_changed TEXT, last_follow_up TEXT, worked_on TEXT, worked_how TEXT);
CREATE TABLE IF NOT EXISTS ema_claim_links (claim_id TEXT PRIMARY KEY, link INTEGER, bill TEXT, first_seen TEXT, last_seen TEXT);
CREATE INDEX IF NOT EXISTS ix_ecl_link ON ema_claim_links(link);
CREATE TABLE IF NOT EXISTS ema_lines (link INTEGER, svc TEXT, cpt TEXT, bill TEXT, claim_id TEXT, map_method TEXT,
  person INTEGER, name_key INTEGER, twin_link INTEGER, primary_payer TEXT, secondary_payer TEXT, payer_group TEXT,
  payer_kind TEXT, plan TEXT, pccn TEXT, units REAL, charges INTEGER, ins_charges INTEGER, ins_paid INTEGER,
  ins_paid_no835 INTEGER, contractual INTEGER, noncontractual INTEGER, nonroutine INTEGER, denial_first_posted TEXT,
  pat_paid_joined INTEGER, pat_adj_joined INTEGER, pat_join TEXT, pat_after_denial INTEGER, allowed INTEGER,
  unit_allowable INTEGER, balance INTEGER, first_posted TEXT, last_posted TEXT, first_ins_paid TEXT, last_ins_paid TEXT,
  zero_pay_rows INTEGER, checks INTEGER, status TEXT, status_class TEXT, action TEXT, action_on TEXT, flags INTEGER,
  as_of TEXT, keep TEXT, PRIMARY KEY (link, svc, cpt)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_el_bill ON ema_lines(bill, svc);
CREATE TABLE IF NOT EXISTS ema_line_payments (link INTEGER, svc TEXT, cpt TEXT, check_no TEXT, posted TEXT, payer TEXT,
  payer_group TEXT, role TEXT, ins_paid INTEGER, rows INTEGER, allowed INTEGER, name_key INTEGER, in_835 INTEGER,
  trace_key TEXT, PRIMARY KEY (link, svc, cpt, check_no, posted, payer)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_elp_check ON ema_line_payments(check_no);
CREATE TABLE IF NOT EXISTS ema_line_reasons (link INTEGER, svc TEXT, cpt TEXT, grp TEXT, code TEXT, reason_class TEXT,
  payer TEXT, contractual INTEGER, noncontractual INTEGER, rows INTEGER, first_posted TEXT, last_posted TEXT,
  PRIMARY KEY (link, svc, cpt, grp, code)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS ema_checks (check_no TEXT, payer TEXT, payer_group TEXT, payer_kind TEXT, shape TEXT,
  damaged INTEGER, first_posted TEXT, last_posted TEXT, rows INTEGER, links INTEGER, lines INTEGER, ins_paid INTEGER,
  zero_rows INTEGER, svc_lo TEXT, svc_hi TEXT, twin_cents INTEGER, match TEXT, trace_key TEXT, trace_payment_date TEXT,
  trace_paid_cents INTEGER, cause TEXT, pair_trace TEXT, PRIMARY KEY (check_no, payer)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS ema_months (month TEXT, payer TEXT, measure TEXT, detail TEXT, cents INTEGER, rows INTEGER,
  PRIMARY KEY (month, payer, measure, detail)) WITHOUT ROWID;
  -- measures: ins_paid, pat_paid, charges, contractual_routine, contractual_denial (detail = reason class),
  -- noncontractual (class), timely, patient_kind (kind|insured or uninsured), product, no835 (detail = C1 cause)
CREATE TABLE IF NOT EXISTS ema_link_map (link INTEGER PRIMARY KEY, bill TEXT, claim_id TEXT, method TEXT,
  votes INTEGER, total INTEGER, conflict INTEGER DEFAULT 0, mapped_at TEXT);
CREATE INDEX IF NOT EXISTS ix_elm_bill ON ema_link_map(bill);
```

**Expected sizes:**
- `ema_ar`: about 10K rows.
- `ema_ar_items`: about 30K rows.
- `ema_claim_links`: grows by about 5K claims a month.
- `ema_lines`: about 75K rows.
- `ema_checks`: about 33K rows.
- In all about 25-30 MB in the in-memory store, estimated from the measured 46 MB for the full tables. Measure it at build; the gate is in 8.3.

### 5.5 New finding columns
In `detect.EXTRA_FINDING_COLS` and `api.FINDING_COLS`:
- `written_off_source` (era_lines | production)
- `written_off_kind` (contractual | noncontractual | moved_to_patient)
- `ema_link`
- `ema_status`, `ema_status_src`, `ema_as_of`
- `ema_balance_cents`
- `ema_worked_on`, `ema_worked_how`
- `source` ('835' | 'ema')

### 5.6 Dashboard copy (`cloud.SERVING_TABLES`; `SERVING_BUILD` "7")

| Table | What is copied |
|---|---|
| `ema_report_files` | all rows, without `origin` |
| `ema_ar` | all rows, without the keys and the hash |
| `ema_ar_items` | the items in `ema_ar` |
| `ema_claim_links` | ids in `ema_ar` or behind findings |
| `ema_link_map`, `ema_lines`, `ema_line_payments`, `ema_line_reasons` | links of bills with findings, links on the A/R, and links of EMA_DENIED items |
| `ema_checks` | rows with `match = 'none'` |
| `ema_months` | all rows |
| meta | without `ema:key_salt` |

## 6. Ingestion, replacement and guards

### 6.1 Identity and order
- `file_id` is the SHA-256 of the report's bytes; a zip member uses its own bytes.
- The same bytes arriving again, by any route or under any name, return "already" and change nothing.
- `order(F) = (as_of, created_at or "", received_at, file_id)`.
- `as_of` is the newest posted date for Production, and the newest of the service date and aging date for the A/R.

### 6.2 Production scope, box and invariant
**Scope.** `gap = posted_lo − svc_lo` in days.
- `gap > 45` gives `posted` scope; otherwise `service` scope.
- A file filtered by service date starts posting the day its services start: measured 0 days for both files. A file filtered by posted date holds postings for services months or years older. So the test cannot tie.

**Box.**
- **Service scope:** svc in [svc_lo, svc_hi] and posted ≤ as_of.
- **Posted scope:** any svc, and posted in [posted_lo, posted_hi].

**Invariant.** For every (svc, posted) point, the ledger holds the rows of the newest applied file whose box contains that point. Rows are never de-duplicated by content.

**`apply_production(F)`** runs in one transaction:
1. `older` = applied files with a lower order whose box meets F's. `newer` = the same with a higher order.
2. Delete the `older` rows inside box(F).
3. Insert F's rows that fall inside no `newer` box.
4. Update `live_rows`. Set statuses:
   - an older file with no live rows left is `replaced`; one with rows left is `partly_replaced`;
   - F is `current`, or `superseded` if every row of F fell inside newer boxes.
5. Add 1 to `seq`.

**What this gives:**
- A weekly posted-date pull replaces its weeks for every service year.
- A service-year pull replaces its year up to its date.
- A late, older pull fills only what no newer pull covers.
- Mixing the two scopes is safe.

### 6.3 Refusals and holds
**Refused** (never applied, no override):
- a required column is missing;
- a column is named twice;
- the claim links come from two EMA hosts;
- the dates are in two orders;
- more than 3,000,000 rows;
- a zip inside a zip;
- on Vercel, a CSV over `REMITWATCH_EMA_STREAM_MAX_MB` (default 450).

**Held** (kept until a person presses Apply or Discard):

| Hold | Condition | Message |
|---|---|---|
| H1 format | rows without dates > 1%, or money-identity breaks > 0.5% | "The layout or format changed." |
| H2 scope | service scope with `charge_line_share` < 97%. Measured: 99.9% on files filtered by service date; 55-68% on a month cut by posted date | — |
| H3 coverage | the rows F would delete cover ≥ 200 links and F has < 90% of those links, or < 50% of their distinct insurance payers (when there are ≥ 10) | "It has N% of the claims the export of <date> had for these dates: was it filtered by payer, provider or location?" |
| H4 double filter | ≥ 20 rows F would delete are rows a genuine export of its scope must contain (service scope: posted before F's first posting; posted scope: service before F's oldest service) | "It seems filtered by both dates." |
| H5 first file | ≥ 2,000 rows and fewer than 3 insurance payers | — |
| H6 (Vercel only) | a Production .xlsx over 120,000 rows, read from the sheet's dimension | "Save it as CSV." |

### 6.4 A/R snapshots
- A snapshot's scope is the calendar years spanned by its service dates.
- **For each year in scope:**
  - If F is newer than that year's current snapshot:
    - F's rows replace the year's rows in `ema_ar`;
    - the history is updated (6.5);
    - `ema_claim_links` upserts every (claim id, link) pair.
  - Otherwise F is superseded for that year.
- **Hold.** A file is held when, for a year whose current snapshot has ≥ 500 rows and is at most 21 days older, F either:
  - lists fewer than 40% of those rows, or
  - lacks a responsible party, coverage type or primary payer that held ≥ 5% of them.

### 6.5 A/R history (`ema_ar_items`)
**`item_key`:**
- Insurance rows: "I|claim id|cpt|svc|coverage|n".
- Patient rows: "P|mrn_key (else name_key)|cpt|svc|balance type|n".
- n is the occurrence in file order: 10 rows repeat another's key.

**When a snapshot becomes current:**
- Items present get `last_seen`, `snapshots` + 1, `last_status` and the balances.
- New items get `first_seen` and `first_status`.
- Absent items of its years get `gone_since` = as_of. An item that comes back clears it.

**Worked marks (W2)** set `worked_on` = as_of and `worked_how` when any of these happens:
- the note hash changed;
- the follow-up date was set or moved;
- a claim action was set;
- the status moved from Denied or Rejected to pending or not sent (a resubmission).

**Retention.** Items gone for more than 120 days are deleted. `ema_claim_links` rows are kept for 1,100 days.

### 6.6 Decisions on held files
`POST /api/ema/reports/{file_id}/decision` with body `{"decision": "apply" | "discard"}`. It requires the login and `require_xhr`.
- **Server:** the decision is recorded, an upload is re-queued and the cycle is woken.
- **Vercel:** a journal action `ema_report_decision` is written (`actions.apply`). The next run fetches the file by its Dropbox id and applies it.
- Apply skips holds, never refusals.

### 6.7 Against the 835s and the other EMA exports
- Nothing is merged into the 835 tables or into `ema_rows`. Nothing is dropped because an 835 also carries it.
- The summaries mark which EMA payments an 835 carries: `in_835` means the check number equals a trace exactly, or as integers when both are all digits.
- The three weekly exports keep their own rows. Where they and the reports answer the same question (a claim's status), the later as_of wins: `ema_sources.created_at` against `ema_report_files.as_of`.

### 6.8 Retention
- Ledger rows whose service date is more than 1,100 days before the ledger's newest posting are deleted at each refresh.
- On the server, the kept copy of a replaced file is deleted once two newer current files cover its box (`REMITWATCH_EMA_REPORT_KEEP=2`). The catalog row stays.

### 6.9 Routes in
**Router:** `dropbox_sync.ingest_new_files(store, paths, log, origins=None)`.
- It calls `ema_reports.recognize` first for .xlsx, .csv, .tsv and .zip files.
- A report goes to `ema_reports.ingest_file(store, path, rec, via, origin, force)`.
- The three old families are read as today, except:
  - a file over 60 MB is refused ("too large for this report");
  - an unknown layout is recorded as `files` kind `ema:unknown`, status `not_read`, with no rows.

**`ingest_file`:**
1. Hashes the file in 1 MB chunks; an already-applied file returns "already".
2. Reads and applies the file (6.2 or 6.4).
3. Writes `ema_report_files`, `files` (kind `ema:production` or `ema:ar`; lines = rows applied; duplicates = rows read minus rows applied) and `ema_sources`.
4. Sets `store.ema_changed`.

**Production files per run:** on the server, all Production files of a cycle are applied in order. On Vercel, one per run (8.2).

**Switch `REMITWATCH_EMA_REPORTS`:**
- `all`: the server default.
- `ar`: the Vercel default until Step 4's gate passes.
- `off`.
- A report the switch does not read is kept as `waiting`, with its origin, and is read once the switch allows.

**Dropbox:**
- `WANTED_EXT` adds .csv, .tsv and .zip.
- `wanted()` keeps the backfill zip (`REMITWATCH_BACKFILL_ZIP`) unwanted.
- For other zips, report members are read; the other members are named and ignored.
- `cloud._ingest_batch` items gain an optional fourth element, `origin` (Dropbox id, path, content hash), passed through to the router.

**Upload page (server):**
1. `accept()` runs `sniff`, then `recognize`.
2. `ema_reports.preview` streams the file once and reports:
   - counts, scope, box and as_of;
   - the files it would replace;
   - any refusal or hold.
   It opens the ledger read-only and takes about 10-15 s for 200 MB. An .xlsx Production export reads only its header and dimension.
3. The file is kept under `uploads/exports/<sha16>/` and queued.
4. `_finish` reads the catalog status.

`uploads._already` words a waiting or held report by its status.

### 6.10 Migration (`migrate_misread`; Step 0; once per host, meta `ema:migrated_v1`)
**Finding the misread sources.** Sources with family `claim_status` in which:
- every row has `claim_id` NULL, and
- some row has `coverage_type`, `adjustment_reason` or `posted_date` set.

A real Claim & Claim Status export always has claim ids.

**For each one:**
1. Delete its `ema_rows`, `ema_sources` and `files` rows.
2. Queue it as `waiting`, from `files.source_path` on the server or from the `dropbox_seen` path on Vercel.
3. If it came from the Upload page, set the uploads row back to queued.

Also delete `ema_rows` of family `unknown`.

**Re-reading.** Step 1 re-reads waiting A/R entries. Step 3 (server) and Step 4 (Vercel) re-read waiting Production entries.

## 7. Summaries and the claim-link map (`ema_ledger.refresh`)

### 7.1 Part A: from the ledger
**When it runs:** when the ledger's seq (server) or its state revision (Vercel) differs from store meta `ema:ledger_seq` / `ema:ledger_rev`, or when retention deleted rows.

**How:** ATTACH the ledger read-only (`cache_size=-32768`, `temp_store=FILE`) and INSERT…SELECT into the store.

**`ema_lines`** has one row per (link, svc, cpt) of linked postings.
- **Patient-day join:** patient-side rows grouped by (person, svc, cpt) are joined to the one linked line with the same keys. Measured: 30,626 groups join one line, 10,665 none, 396 several. The join gives `pat_paid_joined`, `pat_adj_joined` and `pat_after_denial`.
- `balance` = charges − ins_paid − contractual − noncontractual − pat_paid_joined − pat_adj_joined.
- `status` is the newest posting's status.
- `twin_link` is another link with the same (person, svc, cpt).

**Other tables:** `ema_line_payments`, `ema_line_reasons` (non-routine reasons on every kept line, and every reason on lines whose bill has a finding), `ema_checks` (`twin_cents` = the check's insurance dollars on lines that have a twin) and `ema_months`.

**Lean keep rule** (`keep_line`, the same in both modes). A line stays when any of these holds:

| # | Rule | Lines kept by this rule alone |
|---|---|---|
| 1 | a non-routine insurance adjustment (group ≠ PR, code not in `detect.IGNORE_CARCS`) | 6,510 |
| 2 | an insurance payment under a check that is not an 835 trace | 3,193 |
| 3 | its link is not in `ema_link_map`, or is a conflict | 2,818 |
| 4 | its link is in `ema_claim_links` | 22 |
| 5 | its mapped bill has a finding that is open, started, sent or worked | 8,301 |
| 6 | its newest status is not Resolved/Paid | 5,646 |

Measured: 74,554 of 204,359 lines (36.5%) are kept.

### 7.2 Part B: store only
**When it runs:** after Part A, after an A/R change, and after 835s were ingested.

1. **Map**, in order of precedence:
   1. **`ar`:** `ema_claim_links` gives link -> claim id, with bill = `bill_of`.
   2. **`pccn`:** the Payer Claim Control Number equals an 835 claim's.
   3. **`payment`:**
      - Each linked payment whose check is an 835 trace votes for the bill of that trace's lines with the same date and the same code (or original code).
      - Candidates are narrowed in turn to: same name key and same amount; same name key; same amount.
      - A link takes its most-voted bill when that bill has at least 2/3 of its votes. Otherwise the link is a conflict.
   4. **`patient_day`:**
      - Applies to an 835 bill with 2025+ service, inside the ledger's coverage, that has no link yet.
      - The winner is the link whose lines share the most (name key, date, code) with the bill, covering every code of the date with the fewest extra codes.
      - It maps only when one link is top. Ties stay unmapped, which keeps an ASC twin from taking the professional bill.

   If `ar` disagrees with another method, `ar` is kept and the link is marked conflict = 1. Temporary tables with their own indexes stand in for the compact store's missing indexes; `test_efficiency` checks the plans.
2. **Marks:**
   - `in_835` and `trace_key`;
   - `ema_checks.match`: exact; loose (alphanumerics upper-cased with leading zeros dropped, or the last 10 characters equal within the payer group); or none;
   - `ins_paid_no835`;
   - payer groups, from `codes.normalize_payer` after `REMITWATCH_EMA_PAYER_ALIASES`.
3. **C1 causes** (9, C1).
4. **Prune** the lines that now fail the keep rule, with their payments and reasons.

Measured: 82,272 of 84,032 bills with 2025+ service map one-to-one. Part A takes 2 s for two years; Part B a few seconds.

### 7.3 Contract for the checks
- **Memory:** query per bill or per link, in chunks of 500 through a temporary key table. Never load a whole EMA table into Python.
- **The 835 decides wherever it exists.** EMA's money is used where `in_835 = 0`, and for what the practice did. Never add an EMA payment to the 835 payment for the same check.
- **As-of dates:** every EMA figure carries its export's as_of.
- **Open in EMA** means a row in `ema_ar`.
- **Clocks:** the aging date is never a clock. Use the submission date, the A/R's first_seen, the first charge posting or the service date.
- **Exclusions:** product rows and patient-side rows (flags 1 and 16) are never in an insurance check.

## 8. Server and Vercel

### 8.1 Server ([redacted])
- **Ways in:**
  - the Dropbox folder, mirrored to /data and streamed to disk by `DropboxClient.download`;
  - the Upload page (up to 250 MB);
  - later, an inbox folder (Step 9).
- **The two CSVs:** read from disk in one cycle, about 10-12 s each at 33 MB of memory, into `/data/ema_ledger.sqlite` (about 140 MB for two years). Part A and Part B follow, then detection, all in the same cycle's child process.
- **Backups** copy the ledger with SQLite's online backup.
- The switch defaults to `all`.

### 8.2 Vercel: exactly what happens to the 160 MB and 206 MB CSVs
1. **Before Step 3:** nothing happens; .csv files are not wanted.
2. **From Step 3, with the switch on `ar`** (the serverless default):
   - A run that lists a .csv or .tsv over 60 MB opens its download stream, reads the first 64 KB, closes it, and recognises a Production report.
   - It records the file in `ema_report_files` as `waiting` ("read on the server; this site reads Production once its switch is on"), with its Dropbox id, path, size and content hash.
   - It marks the file in `dropbox_seen`, so the Dropbox position moves on.
   - Nothing else is downloaded or kept.
   - A large file that is not a report is marked "(too large)", as today.
   - Zipped CSVs and A/R files take the normal path; a Production member inside a zip is recorded as waiting.
3. **From Step 4.** After the gate (8.3) passes and the owner sets `REMITWATCH_EMA_REPORTS=all` in Vercel and redeploys, each run takes at most one waiting Production file. Three conditions must hold:
   - the file's content hash still matches its Dropbox entry;
   - at least 150 s of the 240 s budget remain;
   - the run's peak is below 70% of the function's memory.

   The run then:
   1. downloads `ema_ledger.sqlite.gz` (absent the first time) and unpacks it to /tmp as a scratch copy (about 140 MB at two years);
   2. streams the CSV from Dropbox (`download_chunks`, 4 MB pieces) straight through the reader into the scratch ledger. No copy of the CSV is written anywhere; this uses about 33 MB of memory, and the reader checks the deadline every 10,000 rows. A Dropbox entry whose content hash is already in the catalog is skipped before any download;
   3. applies it (6.2) and runs the guards. A hold or refusal leaves the scratch copy unused;
   4. runs Part A and Part B into the in-memory store;
   5. gzips the ledger at level 1 (about 33 MB) and uploads it to the state folder with `put_chunks`. The returned revision goes into store meta `ema:ledger_rev`, and the scratch copy is deleted;
   6. sets `cloud:detect_pending`, saves the store, then the dashboard's copy, then the Dropbox position, and calls the next run (`more=True`).

   The 2025 file is run 1, the 2026 file run 2, and detection run 3 (MAX_HOPS allows 30). After that only weekly pulls arrive. A 5-week posted-date pull is about 20 MB, so it takes the normal path.
4. **Failures:**
   - **Past the deadline:** the big-file path has its own deadline (t0 + budget − 75 s). Past it, the run throws its scratch copy away; the copy in the state folder is untouched. It counts an attempt and leaves the file waiting.
   - **After 3 attempts** the file is held: "too slow to read on this site: zip the CSV (about 12-15 MB) or let the server read it".
   - **Crash between saves:** a run that dies after saving the ledger but before saving the store is repaired by the next run. The revisions differ, so Part A runs and the catalog is rebuilt from `ledger_files`. The Dropbox position was not saved, so the file is listed again and skipped as already applied.
5. **Limits:**
   - a CSV over 450 MB is refused ("export it in two service-date ranges");
   - a Production .xlsx over 120,000 rows is held ("save it as CSV");
   - a zipped CSV of up to 60 MB is downloaded whole and read member by member.
6. **Normal runs never touch the ledger.** Detection reads the lean tables in the store. A change to `REMITWATCH_EMA_PAYER_ALIASES` re-runs Part B only.

### 8.3 Release gate for Production on Vercel (Step 4)
Run `scripts/cloud_bench.py` on a copy of the live state folder with the real files, under a 2 GB memory limit, using FakeDropbox and `REMITWATCH_EMA_REPORTS=all`.
- Each apply run must peak under 1.75 GB.
- Each apply run must finish in under 120 s locally. That leaves room for a download at 2 MB/s or better; the Dropbox-to-Vercel speed was never measured.
- The detection run that follows must peak under 1.75 GB.

**If the gate fails,** Vercel stays on `ar`:
- the A/R and every A/R check still work there;
- the Production checks show on the server only;
- the Data tab says so.

**[Owner decision O2]:** if the Vercel site is being retired, keep it on `ar` and skip Step 4.

## 9. Checks

Each check lists its inputs, rule, exclusions, output, and the figure expected on the real files.

### C1. Insurance payments with no remittance (report only; Step 6)
**Inputs:** `ema_checks`; `transactions` (superseded = 0); the twins and the map.

**Window:**
- Checks first posted between the first 835 payment date and the newest 835 payment date + 3 days are judged.
- Later checks are `after_feed`, shown as "since the remittances stopped". Measured: $1.44M on 1,844 checks, Oct 10 to Nov 20.

**Rule.** A check matches when it matches an 835 trace exactly or loosely (7.2). An unmatched check gets the first cause that applies:
1. **`misnumbered`:**
   - an 835 of the same payer group whose own trace matches no EMA check (C4);
   - dated −45 to +10 days from the check's first posting;
   - with a claim-paid total within $1 of the check's insurance payments.

   The two are paired both ways (`pair_trace`), so C4 lists neither.
2. **`separate_payee`:** at least 80% of the check's dollars are on lines whose twin link maps to an 835 bill. These are the ASC's facility claims, paid to another payee.
3. **`no_era_va`:** the payer kind is va, and the payer group has no matched check in the 365 days before.
4. **`no_era`:** no matched check under that EMA payer name in the 365 days before.
5. **`feed_gap`:**
   - the payer group has at least 5 matched checks in the 365 days before;
   - and this check's shape (digits → 9, letters → A, runs kept) is the shape of at least 20% of them.

   The 835 exists; it never reached the feed.
6. **`other`.**

**Exclusions:** checks with no insurance money (status-only rows), damaged check numbers (listed separately), product rows and patient-side rows.

**Output (no worklist items):**
- `ema_checks.cause`, and `ema_months` measure no835 by cause.
- `/api/ema/reconcile`: totals by month, cause and payer.
- `/api/ema/checks.csv?cause=&from=&to=`: check number, first posted date, EMA payer, amount and cause. This is what TriZetto or the payer needs to re-send; it holds no patient data.
- Data health `remits_missing`: not ok when feed_gap + no_era + no_era_va in the last full month exceed max($5,000, 2% of matched).
- Alert `REMITS_MISSING`: per payer group and month, when feed_gap reaches $2,000 on at least 2 checks.

**Expected (Jan 2025 to Sep 2026):** $2,742,788 of $21,782,808 unmatched (12.6%).

| Cause | Amount |
|---|---|
| separate_payee | $923K |
| VA | $849K |
| no_era | $255K |
| feed_gap | $609K |
| other | $107K |

Every one of these blocks is invisible to the 835 rules. The feed_gap and VA lists are the re-send and ERA-enrollment requests.

### C2. Findings EMA wrote off (Build 1 v2; marks existing items only; Step 5)
**Inputs:**
- Open, started or sent findings in denial, bundling, downcoding and underpayment. EMA-only items mark themselves.
- The links of their bill (from the map, with no conflict).
- `ema_line_reasons` and `ema_lines`.

**Which postings count.** On the finding's line: the link, the date, and a code among the procedure code, original code and billed code. A whole-claim item uses the codes and dates in its evidence. A posting counts when it is:
- an insurance adjustment (contractual or non-contractual);
- with a non-routine reason, or the finding's own CARC;
- not in group PR.

**The figure.**
- Postings are summed signed, so reversals net out. The total counts only when it is above 0.
- It applies only where the claim carries the code once on that date (`_code_once`).
- `written_off_cents` = max(the ERA-lines figure, min(net, shortfall)). The two figures are never added.
- `written_off_on` = the newest posting counted.

**Fields set:**
- `written_off_source`: era_lines or production.
- `written_off_kind`:
  - contractual or noncontractual, by the larger part;
  - moved_to_patient when the insurance side was closed by a CO reason and patient money was posted on the line after it, beyond the visit day. This carries the hint "under most agreements a CO reason is not the patient's to pay".
- `ema_link`, `ema_status`, `ema_balance_cents`, `ema_as_of`.

**Output.** The existing step "Work what EMA wrote off", with its chip and badge; the step now shows "N on a denial reason, M posted as contractual". A new chip "Written off as contractual". No new items.

**Expected.** Build 1 marks rise from 17 to about 901 items ($83K) inside RemitWatch's deadline. In all, 3,086 open findings for 2025+ service ($291K) are at zero balance in EMA.

### C3. Unanswered and unsent claims (Build 2 v2; Steps 2 and 5)
All of this lives in `Detector.run_ema_open_claims`. There is one item per claim (`_fid('EMA_OPEN_CLAIM', claim id)`), and today's hand-overs are unchanged.

**a. Status dictionary** (`ema_checks.status_class`; exact words, case-insensitive):

| EMA status | Class |
|---|---|
| Payment Pending, Insurance Reprocessing, Medical Records Sent | pending |
| Denied | denied |
| Rejected | rejected |
| Batched for Printing, Batched for Sending, Invalid | not_sent |
| Ready for Posting | answered |
| Resolved/Paid | paid |
| Resolved/Other | settled when an insurance answer is on file, else gave_up |
| Inactive/Voided | void |
| "-", None | none |

- An unknown word falls back to today's regular expressions and is listed on the Data tab (meta `ema:unknown_status_words`).
- The status comes from the newest of the A/R, Production and the Claim & Claim Status export, by as_of.

**b. NOT_SENT** (new signal; Step 2).
- **Trigger:** the status class is not_sent for 14 days or more since the first charge posting, else the bill's creation date, else the service date. For Invalid, 3 days.
- **Item fields:**
  - category denial; actionable correct_and_resubmit; work type fix; the claim's id;
  - deadline: the first-filing limit from the service date; high severity past 30 days;
  - title "Never left EMA: <status> since <date>: <codes>".
- **Expected:** 65 lines ($9.7K), 30 of them over 30 days old. The 43 NO_ANSWER items EMA shows as Batched for Printing move here.

**c. Candidates from the A/R** (Step 2): current insurance debit rows whose status is pending and that are not already candidates.
- **Lines:** A/R-only candidates take their lines from the A/R rows: code, date, and the balance as the amount billed.
- **Same exclusions:** they pass through the same exclusions as candidates from the exports (live items, visits paid under another bill, a newer answered submission, self-pay). One excluded that way is counted in the A/R panel as "paid elsewhere, still open in EMA".
- **Due date:** due = sent + max(NO_ANSWER_DAYS, the payer group's p95 days to a first answer). Add 30 days for Secondary or Tertiary coverage.
- **Sent date:** the Claim & Claim Status submission date, else the A/R's first_seen, else the first charge posting, else the service date.
- **The p95:**
  - computed from the 835s: CLP02 1, 19 or 4; 2025+ service; payer groups with at least 20 bills, else 45 days;
  - stored in meta `ema:turnaround`;
  - quoted in the item, e.g. "Anthem answers 95% of claims within 41 days".

**d. Clocks:**
- **A/R candidates** are judged against their A/R: due ≤ A/R as_of − 3 days, and the A/R must be at most 8 days old. The A/R itself says the claim is still unpaid on that date, with or without an 835 feed.
- **Candidates from the exports** keep today's clock (the newest 835) until Production is in. Then (Step 5) answers_through = max(newest 835, Production as_of − 8 days), when both hold:
  - the payer group has an EMA posting in the 30 days before that as_of;
  - Production is at most 14 days old.

**e. Answers from EMA** (Step 5). A candidate is answered when its line shows, posted after the claim was sent, any of:
- a net insurance payment;
- a non-routine CARC;
- a $0 check row (a $0 ERA answer).

The line is found by, in order:
1. claim id → link through `ema_claim_links`;
2. else bill → link through the map;
3. else a patient bridge: EMA patient id → name key, learned from charge-line rows whose claim id has an 835 claim with that key (unique keys only), plus date, code and payer group.

Payments under `separate_payee` checks never count as answers.

Expected: of the 124 NO_ANSWER items that can be checked, 78 ($24.5K) leave because EMA posted payment by Oct 9 under checks with no 835, and 21 ($8.8K) were paid after Oct 9.

**f. Other A/R statuses:** Rejected → REJECTED_PRE_PAYER; Invalid → NOT_SENT.

**Expected for Step 2.** Payment Pending past p95: $56.2K.
- About 90 primary claims ($54.1K), 157 of whose rows have no note.
- 61 secondary rows ($2.2K).

### C4. 835 payments not posted in EMA (REMIT_NOT_POSTED; Step 6)
**Inputs:** `transactions`, `ema_checks`, and Production coverage (the boxes in `ema_report_files`).

**Rule.** One item per 835 transaction (superseded = 0, paid > 0) when all of these hold:
- Production covers it: either a current service box holds all its claims' service dates with as_of ≥ payment date + 21 days, or posted boxes cover payment date − 3 days to + 21 days.
- Its trace matches no EMA check, exactly or loosely.
- Its claims' paid total is over $1.
- It is dated within the last 365 days.
- It is not paired as misnumbered (C1).

**The item:**
- category fees; actionable review; new work type `post` ("Post the payment"):
  - find the deposit;
  - if it was posted under another number, fix the number;
  - if it never arrived, ask the payer to reissue.
- expected = the claims' paid total; no deadline; high severity from $1,000.
- Title: "Paid $X by <payer> on <date> (trace ending 1234): not posted in EMA".

**Posting differences.** Matched traces whose EMA total differs from the 835 by more than max($1, 0.5%):
- same-payer, same-day pairs that net within $5 are report only ("cross-posted");
- a net short of $25 or more becomes an item, "posted $Y short".

REMIT_NOT_POSTED is added to `actions.NEVER_HIDDEN`.

**Expected:** 232 traces ($43.8K) over 20 months: self-funded TPAs $24K, Humana MA $5.4K, UMWA $5K; Sep 2026 alone $12.7K. Fewer fall inside 365 days.

### C5. Denials only EMA saw (EMA_DENIED; Step 7)
**Inputs:** `ema_lines`, `ema_line_reasons`, `ema_ar` (Denied rows), the map, 835 lines and claims, findings.

**Rule, per (link, date, code).** All three must hold:
1. **A denial:** non-routine insurance adjustments (group ≠ PR) net to at least $5, or an A/R Denied row has a denial amount and no CARC posting.
2. **No 835 coverage:** no 835 line for (bill, date, code or original code), and no 835 claim-level denial or finding for that bill and date. An unmapped link counts as having none.
3. **Not paid since:** insurance payments after the first denial posting are under 50% of expected.

**Exclusions:**
- G2211, 99024 and charges of $1 or less, as in detect.py's zero-paid rule;
- products, patient-side rows, voided lines, and denials that net to 0.
- An 835 line with no finding never produces an item: the 835 rules decide. Those lines are reported in C7 as "835 lines RemitWatch does not list".

**One item per (link, reason class),** id `_fid('EMA_DENIED', link, class)`:
- **Category:** bundling for the bundling class; downcoding for the level class on a visit code; denial otherwise.
- **carc:** the largest reason.
- **Work type:** as for CLAIM_DENIED_UNPAID: auth, note or rule by CLAIM_KIND_CARCS; fix for coding or information reasons; check otherwise.
- **Expected amount,** first available, capped at the charge, with its basis stated:
  1. the usual rate × units;
  2. EMA's allowed amount;
  3. the charge × the payer group's paid/charge ratio for the code over the last 12 months (`exposure`, at least 10 paid lines);
  4. 45% of the charge.
- **Deadline:** (the first denial posting − the payer group's p95 posting lag, default 8 days) + the `appeal_rule` days. The basis says "counted from EMA's posting less its usual delay; the EOB's date can be earlier".
- **Severity:** high when due within 30 days and expected is at least $100.
- **patient_control_number:** the link's claim id (`ema_claim_links`), else the bill, else NULL.
- **source** 'ema'.
- **Evidence:** the lines as [code, date, charge, reason, posted, contractual], plus `ema_link`.
- **ASC facility claims** (`separate_payee` links) are titled "ASC facility claim: …" (**[Owner decision O9]**).
- The item marks its own write-offs (C2) and closes on EMA payments (C6).

**Hand-over:**
- `save()` gains `ema_heirs`, keyed (bill, date, code). The first 835 finding on that line inherits a worked EMA_DENIED item:
  - its status: Started if the EMA item was started or sent, else To do;
  - its notes;
  - an event `signal_changed`.

  The EMA item then goes stale.
- A worked NO_ANSWER or NOT_SENT item for the same claim id hands its work to the EMA_DENIED item, as the claim's answer.

**Expected:**
- 1,331 lines ($217K in charges); 460 of them still inside the window (about $41K expected).
- $30.7K posted since the feed stopped, growing about $5K a week until the 835s return.

### C6. Close or confirm what EMA shows paid (Step 5)
**Where it lives:** in `resolve_elsewhere`, after today's EMA branch; `EMA_CLOSES` applies. It is extended to downcoding, underpayment and EMA_DENIED findings.

**Payments counted.** Production insurance payments on the finding's mapped line that are all of these:
- posted after the finding's payment date;
- under a check that is neither the finding's own trace nor an 835 trace dated on or before it;
- not under a `separate_payee` check;
- on a claim that carries the code once on that date.

**Outcome:**

| Payment | Result |
|---|---|
| Same payer group (EMA payer after aliases vs the finding's group), at least 50% of expected | `_mark_paid`, with the note "EMA shows $X paid by <payer> on <date> (check ending 1234, postings through <as_of>). Reopen it if that payment is for something else." Downcoding and underpayment close only when the later net payment is at least 95% of the shortfall |
| Another payer group only, at least 50% | a hint about a second payer |
| Under 50% | a "partly paid per EMA" hint |

- The (check, date, link) goes into `used_payments`.
- A newer pull that no longer shows the payment reopens the item, as today.

**Expected:**
- 259 closures ($28.3K): denials 199 ($22.5K), bundling 48 ($4.1K), downcoding 5, underpayment 7.
- 137 other-payer hints ($16.6K); 109 partial hints ($17.0K).
- EMA also corroborates 3,252 of the 3,300 findings RemitWatch already resolved (98.5%).

### C7. Denial reasons posted as contractual (EMA_DENIAL_AS_CONTRACTUAL; Step 7)
**Rule:**
- One item per payer group × reason × posted year, when contractual-labelled non-routine postings reach $1,000 on at least 10 lines.
- Category contract; actionable review; work type check.
- High severity from $25,000. The item is outside the money totals.

**Evidence:**
- the top codes;
- the lines split into: under a C2 mark; under C5; out by policy (G2211, 99024, $1 or less); other 835 lines;
- the part still inside its window;
- the value at the payer's paid/charge ratio.

**Next step:** "Have EMA post this reason as non-contractual so it reaches the denial queue; then decide per code: appeal, bill it differently, or stop billing it." G2211_DENIED's evidence also gains the EMA figures.

**Expected:** $459K in charges (about $228K):

| Part | Amount |
|---|---|
| Under C2 | $190.7K |
| Under C5 | $86.7K |
| Out by policy (mostly G2211) | $175.6K |
| Other | $4.2K |

The new money is about zero. The value is fixing the posting rule, and seeing the 2026 surge of 77600 and G2211 at Anthem.

### C8. Credits (report; Step 2)
- **What:** A/R credits by party, coverage and age from the service date.
- **Data health `credits`** is not ok when either:
  - any Medicare primary-coverage line credit is over 60 days old (Medicare's 60-day overpayment rule); or
  - other credits over 60 days total $500 or more.
- **CSV:** `/api/ema/credits.csv` with initials, service date, code, amount, age, party and coverage. No names, no MRN.
- **Expected:** $37.1K in credits.
  - Insurance −$2.5K, with Medicare primary exposure about $0.
  - Patient −$34.6K: −$4.8K (71 rows) over 60 days, i.e. refunds due; −$29.8K under 60 days (cosmetic prepayments).
- **[Owner/compliance decision O8].**

### C9. Timely-filing write-offs (report; Step 7)
- CO-29 write-offs by month and payer, with whether an item covers each.
- Expected: $30.0K (2026: $26.3K). $23.1K is still inside a window and is already itemized by C2 or C5.

### C10. Patient-side write-offs on insured lines (compliance report; Step 7)
- EMA's adjustment kinds on patient days joined to insured lines that have no insurance denial.
- Expected: $155K. Pro Courtesy $64K, MCR QMB $21K, Hardship $17K, Collection $16K, Bad Debt $11K.
- RemitWatch only lists these. A compliance lead decides (routine waivers, the QMB billing ban) **[O8]**.

### C11. Patient A/R by age (report; Step 2)
$47.2K, with product rows shown separately.

### Workflow overlays

**W1. Open in EMA** (Step 1). Every item, claim view and A/R list row that has a link gets an "EMA ↗" link.

**W2. Worked in EMA** (Step 8). `ema_worked_on` and `ema_worked_how` take the newest of:
- the A/R note changed;
- the follow-up date was set or moved;
- a claim action was set;
- a resubmission: Denied or Rejected → pending, or a higher submission suffix after the finding's remittance;
- a Production claim action matching APPEAL, CORRECTED or REPROCESS;
- a non-routine posting after the finding.

It never changes an item's status.

**W3. Unworked** (Step 8). Items that are all of:
- in a money category;
- To do;
- inside their window;
- not stale;
- with no EMA work in the 21 days before data_through.

Written-off items are included, with the split shown. Filter `ema=unworked`; it is also Today's header box.

**W4. A/R accounted for** (v1 in Step 2, v2 in Step 8; detection writes it to meta `ema:ar_accounted`). The insurance A/R in buckets that add up to its total; each bucket opens its list:
- waiting inside the payer's usual time;
- past it (NO_ANSWER);
- denied with an item: To do, Started, Sent, or worked in EMA;
- denied with no item;
- rejected or never sent;
- paid elsewhere but still open in EMA;
- settled since the pull (v2: Production shows a payment or write-off posted after the A/R's as_of);
- other statuses;
- credits.

**W5. Fax stale hold per item** (Step 5). `appeals_send` holds an item as stale only when neither the 835s nor Production can vouch for it. An item is not stale when all of these hold:
- Production is at most 14 days old;
- the item's link is mapped, with no conflict;
- no insurance payment was posted on its line after its remittance;
- the line is on a current A/R (at most 8 days old), or its EMA balance is over $0.

EMA_DENIED items are held with code `no_remittance`: "No remittance on file: get the EOB from the payer's portal, then Fax it anyway."

## 10. Detection wiring
**Rules version.** `DETECT_RULES` becomes 2.7 (Step 2), 2.8 (Step 5), 2.9 (Step 6), 2.10 (Step 7) and 2.11 (Step 8). `_rules_hash` adds `ema_reports.py`, `ema_ledger.py` and `ema_checks.py`.

**Order in `Detector.run()`:**
1. `build_links`
2. map refresh if `ema:map_dirty` (Part B)
3. `load`
4. baselines
5. line rules
6. claim rules
7. `run_ema_open_claims` (C3)
8. `emit_ema_denials` (C5)
9. aggregate rules, plus C7
10. second rates
11. `resolve_elsewhere`, plus C6
12. `audit_postings` (C4)
13. `mark_written_off` (C2) and the overlays (W2, `ema_status`)
14. `save`, plus `ema_heirs`
15. `reconcile_summary`: C1, C8-C11 and W4 into meta

**Signal lists.** `EMA_CLAIM_SIGNALS` gains NOT_SENT. `actions.NEVER_HIDDEN` gains NOT_SENT and REMIT_NOT_POSTED.

**insights.py:**
- `WORK_TYPES` gets `post` ("Post the payment") before `check`.
- `_WORK_OF`: post = {REMIT_NOT_POSTED}; fix gains NOT_SENT.
- `work_type` and `kind_sql` treat EMA_DENIED as they treat CLAIM_DENIED_UNPAID.

| Signal | Label | Short label |
|---|---|---|
| NOT_SENT | Claim never left EMA | Not sent from EMA |
| REMIT_NOT_POSTED | Paid by 835, not posted in EMA | Payment not posted |
| EMA_DENIED | Denied in EMA, no remittance on file | EMA denial, no 835 |
| EMA_DENIAL_AS_CONTRACTUAL | EMA writes off this payer's denials as contractual | Denials posted as contractual |

**codes.py:**
- The VA rule moves above OPTUM and gains VACAA. `VA CCN OPTUM` then becomes VA Community Care (90-day appeal, 180-day filing), and `WPS-VACAA` becomes VA.
- `REMITWATCH_EMA_PAYER_ALIASES` (JSON, set in the host's environment only, empty in the repo) maps practice-specific EMA payer names before `normalize_payer` **[O3]**.
- Changing codes.py re-analyses the archive once.

## 11. Routes and pages

### API
**New routes:**
- `GET /api/ema/reports` (both modes): the catalog (without origins), coverage (Production boxes; A/R years and as_of), held files, the switch, and the claim URL template.
- `POST /api/ema/reports/{file_id}/decision` (6.6).
- `GET /api/ema/reconcile`: counts and dollars only.
  - freshness and map coverage;
  - C1 by month, cause and payer;
  - totals since the feed stopped;
  - the C4 summary, C7, C8-C11, and the A/R buckets.
- CSV exports, all no-store: `GET /api/ema/checks.csv`, `GET /api/ema/unposted.csv`, `GET /api/ema/credits.csv`.
- `GET /api/ema/ar?bucket=waiting|chase|denied|denied_no_item|not_sent|paid_elsewhere|settled|other|credits&page=`: claim id, link, payer group, service date, code, balance, status, days, and whether a note exists. No names.

**Changed routes:**
- `GET /api/meta` adds `ema`: {ar_as_of, production_through, held, claim_url}.
- `/api/findings`, `findings.csv`, `worklist/counts` and `desk` take the filter `ema=written_off|contractual|paid_other|on_ar|ema_only|unworked|worked`. It goes through `api._filters`, so counts, tiles and lists agree.
- `GET /api/findings/{id}` and `/api/claims/view` add an `ema` block:
  - the links;
  - the A/R rows, with first_seen and the worked marks;
  - the lines, with payments marked 835 or no 835;
  - write-offs by reason; the balance, status and as_of;
  - on the server, the bill's ledger postings (newest 500, no keys); on Vercel, a note that the postings are on the server.
- `/api/claims/search` also finds bills by the claim ids in `ema_ar` and `ema_claim_links`.
- `/api/brief` adds `ema`: {ar_as_of, production_through, banner, unworked, closed_week, paid_other, not_sent, not_posted, ema_only, written_off_split, the A/R buckets}. Every number equals the count line of the list it opens.
- `POST /api/appeals/send` applies W5.

### Pages
**Upload page:**
- A guide section, "Every week: EMA's A/R and Production reports" (13.1).
- `describe()` lines: scope, box, as_of, rows, what it replaces, what was left out, and "names, dates of birth, record numbers, billing notes and staff names are never kept".
- A panel, "EMA reports on file", with Apply and Discard for held files.

**Data tab:**
- The same reports panel.
- "EMA against the remittances": C1 by cause for 12 months, with the CSV buttons.
- Credits, unknown status words, and map coverage.

**Today:**
- **Info banner,** shown while the 835s trail Production by more than 14 days: "Remittances stop at <date> (TriZetto's SFTP login is pending). EMA's postings through <date> stand in for them: paid items close, unanswered claims are judged, and the denials EMA posted are listed. Downcoding and underpayment on visits paid since then wait for the remittances."
- **Plan steps:**
  - "Work what EMA wrote off", with its split;
  - "Chase claims the payer never answered", with "EMA's postings and A/R were checked: N claims EMA shows answered were left out";
  - "Send what never left EMA" (NOT_SENT);
  - "Work EMA's denials no remittance showed" (EMA_DENIED);
  - "Post what EMA is missing" (C4).
- **Header boxes:** "Nobody has touched: $X in N items" and "Closed from EMA's postings this week: N ($Y)".
- **Panel:** "EMA's A/R, accounted for (as of <date>)".

**Worklist (`desk.js`):**
- "EMA ↗" on each row with a link.
- Badges: "no 835", "EMA: <status> $<balance>", "worked in EMA <date>", "EMA only".
- The `ema` chip row.
- Note text never appears.

**Item drawer (`features.js`):** an "In EMA" box with:
- Open in EMA;
- the status and its source; the balance;
- up to 10 postings after the remittance;
- the follow-up date;
- "EMA has a note, changed <date>";
- for chase items, the payer's turnaround.

**Claim view (`claim.js`):** Open in EMA per link; "EMA's A/R now"; and "EMA postings" on the server.

**Bigger wins and Progress:**
- Bigger wins: "Write-offs that hide denials" (C7); "Payments that arrive without a remittance" (C1 by cause, each with its action).
- Progress: "Collections and write-offs" by month (`ema_months`).

**Header:** "EMA as of <date>" beside the feed chip.

### Data health, alerts and email
**Data health items:**
- `ema_reports_missing`;
- `ema_ar_stale` and `ema_production_stale` (more than 8 days old);
- `ema_report_held`; `ema_columns`;
- `ema_map` (under 90% of covered 2025+ bills mapped); `ema_link_conflicts` (over 2%);
- `remits_missing`; `credits`; `ema_status_words`.

The existing `feed` item stays `ok: false` while the 835s are behind, and its text says what EMA covers in the meantime.

**Alerts:** `EMA_STALE` (either report more than 8 days old; it clears itself) and `REMITS_MISSING`.

**Weekly email (`report.py`):** the unworked number, items closed from EMA, write-offs inside the window, and the reports' as-of dates.

## 12. Tests (invented data only)

### `tests/ema_fixtures.py`
- `AR_COLUMNS` (22) and `PRODUCTION_COLUMNS` (39) as exact strings.
- `production_row(**kw)` and `ar_row(**kw)`, with invented values only:
  - patients ("Testpatient, Alpha"), DOBs, MRNs, notes;
  - staff ("Staff One") and clinicians ("Clinician One");
  - claim ids in EMA's shape ("ZZ00TEST1C001");
  - 8-digit links and check numbers.
- Link URL `https://[ema host]/ema/ws/v9/qlikRedirect/claim/{n}`.
- Writers:
  - `production_csv(rows, crlf, bom, title, delimiter)`, `production_xlsx`;
  - `ar_xlsx(rows, created)`, `ar_csv`;
  - `zipped(name, data)`;
  - `big_production_csv(path, n)`, which streams rows to disk.

### `tests/test_ema_reports.py`
1. The two reports are told apart by their exact headers; the three weekly exports read exactly as before; report rows never reach `ema_rows`; `detect_family` returns `production` or `ar_report`.
2. Cells parse as EMA writes them:
   - $1,234.56, ($12.34), $45., -, None, empty;
   - M/D/YYYY, Excel serial dates, negative units;
   - CARCs, EMA's kinds, products;
   - check numbers with leading zeros, and in scientific notation (counted);
   - day-first and mixed date orders.
3. CSV, xlsx and zip copies of the same rows give the same ledger. A 200K-row file reads under a 64 MB tracemalloc peak.
4. Privacy:
   - no fixture name, DOB, MRN, note, staff name, clinician name or "://" reaches any text column of the store, the ledger or the dashboard's copy, or any log line;
   - `ema:key_salt` is never in the dashboard's copy.
5. A scan of the repository finds no `*.ema.md` host except the fixture label.
6. The URL prefix:
   - is accepted from a fixture file;
   - is refused for another domain, for `javascript:`, and for two prefixes;
   - the setting overrides it, and `off` turns links off.
7. Missing required columns refuse the file and name the columns.
8. A/R:
   - each year is replaced on its own, and an older file never replaces a newer one;
   - the history (first_seen, gone_since, worked marks) is kept;
   - the shrink hold works.
9. Migration: an A/R that the old code loaded as claim_status is purged and read again. Tested on the server (mirror and upload) and serverless (the `dropbox_seen` path).
10. Step 0: a report that is uploaded or synced is kept as waiting, and is read once the step that reads it ships.

### `tests/test_ema_ledger.py`
1. Identical postings are both kept; the same file twice changes nothing.
2. Scope:
   - service-year and 13-week service-date files are `service`;
   - a 5-week posted-date file is `posted`;
   - the charge-row share hold works.
3. Boxes: posted after service, service after posted, a late older file, and three files applied in every order all give the same ledger.
4. Holds H1-H5 with Apply and Discard work; a refused file cannot be applied.
5. Summaries add up to hand totals:
   - primary, secondary and other payments, and the balance;
   - the patient-day join (one, none, several) and twins;
   - each of the six keep reasons, and pruning after mapping.
6. Map:
   - `ar`, `pccn`, payment votes (including a conflict), and `patient_day` (an ASC twin left unmapped);
   - checks are found exactly, and with leading zeros.
7. Retention at 1,100 days.

### `tests/test_ema_checks.py`
835s are built with `tests.test_features.build_store` / `svc` and `tests.test_remitwatch.remit`.
- **C1:**
  - every cause;
  - the misnumbered pair, which C4 then does not list;
  - the after_feed window and the alert threshold.
- **C2:**
  - the cap, reversals and the finding's own CARC;
  - the max (never the sum) with the ERA lines;
  - a code twice on one claim, and moved_to_patient.
- **C3:**
  - the dictionary: Batched → NOT_SENT; Ready for Posting is answered; Resolved/Other both ways; unknown words are listed;
  - an A/R candidate past p95 is listed while the 835s are stale;
  - an EMA payment or CARC answers a claim, but a separate_payee payment does not;
  - secondary coverage gets +30 days.
- **C4:**
  - an unposted trace becomes an item;
  - the coverage and lag guards;
  - a cross-posted pair is report only;
  - NEVER_HIDDEN.
- **C5:**
  - CO-197 with no 835 becomes an item; none is made when there is an 835 line or a claim-level denial;
  - the exclusions;
  - the deadline counts from the posting minus the lag;
  - when the 835 arrives, status and notes are handed over;
  - a worked NO_ANSWER hands its work to the EMA_DENIED item.
- **C6:**
  - the same payer closes the item; another payer gives a hint; a partial payment gives a hint;
  - a separate_payee payment never closes;
  - a person's status stands;
  - downcoding needs at least 95% of the shortfall.
- **Other checks:**
  - C7 is aggregate only; C8's thresholds hold.
  - W3 equals Today's number; W4's buckets add up to the total; W5's stale rule holds.
  - every `ema` filter's count equals its list.
- **codes:** `VA CCN OPTUM` and `WPS-VACAA` normalize to VA Community Care.

### `tests/test_ema_vercel.py` (FakeDropbox, LocalState)
- A big CSV is recognised from its first 64 KB and kept waiting under `ar`.
- Under `all`:
  - it is streamed, the ledger is saved to the state folder, and detection is deferred to the next run, which detects;
  - one Production file is read per run.
- A run killed after the ledger save is repaired by the next.
- A slow stream throws its scratch copy away and retries; the file is held after 3 attempts.
- A zipped CSV takes the normal path.
- The dashboard's copy has the EMA tables but not the salt.
- The same fixtures through `run_cloud_cycle` and `pipeline.run_cycle` give the same findings (test_parity style).

### Existing tests
- **test_insights:** the WORK_TYPES order becomes fix, auth, note, rule, rate, chase, post, check; the new labels.
- **test_uploads:**
  - both reports are previewed without a whole read;
  - the held flow works;
  - the same report by Dropbox and by upload gives the same store.
- **test_sync:** .csv and .zip files are routed; an unknown CSV is not loaded; old families over 60 MB are refused.
- **test_cloud:** the size messages; serving build 7.
- **test_efficiency:** the plans of Part B and of every new detection statement use their indexes.
- **test_claims:** the `ema` block; no names.
- **test_desk:** the `ema` filters.

## 13. Docs
- **OPERATIONS.md:**
  - a new section, "EMA's A/R and Production reports": what to export, the settings, cadence, where files go, what each check does, held files, and Vercel;
  - amend "What EMA shows as paid", "Written off in EMA", "Claims the payer never answered", "EMA by hand", and the daily and weekly routine.
- **ARCHITECTURE.md:**
  - an "EMA reports" section: recognition, the ledger and its box invariant, the lean summaries, the map, the checks, memory and sizes;
  - a serverless bullet for the ledger in the state folder;
  - an update to the note on how long the store can grow.
- **SERVER_RUNBOOK.md:** the ledger's location, backups, disk use, and the CLI `ema-reports` (`--status`, `--summarize`, `--reread <file_id|all>`, `--decide`). `backup` and `import-cloud` include the ledger; `compare-cloud` compares the summary tables.
- **VERCEL_SETUP.md:** the switch, the gate, and the timing.
- **MISSING_DATA.md and TRIZETTO_REQUEST.md:**
  - missing remittances, by cause;
  - ask whether the ASC's TIN can deliver its 835s to the same SFTP login;
  - ERA enrollment for VA community care.

  No numbers or names from the files go in.
- **CONTEXT_CHECKPOINT.md:**
  - the reports have arrived;
  - the ASC question is answered: its facility fee is posted in EMA, and its 835s go to another payee;
  - the standing reminder about the AR report closes.
- **README and .env.example:** `REMITWATCH_EMA_REPORTS`, `REMITWATCH_EMA_LEDGER`, `REMITWATCH_EMA_STREAM_MAX_MB`, `REMITWATCH_EMA_CLAIM_URL`, `REMITWATCH_EMA_PAYER_ALIASES`, `REMITWATCH_EMA_REPORT_KEEP`, `REMITWATCH_EMA_STALE_DAYS`.

### 13.1 The routine for the practice (the Upload page's guide)
**Every Monday, the A/R report:**
- all open A/R, insurance and patient, for every payer, provider and location;
- all 22 columns, saved as Excel;
- one file, or one per service year.

**Every Monday, the Production report:**
- all 39 columns; no filter except the date; saved as CSV (zip it if it is over 60 MB);
- **Preferred:** Posted Date from five weeks ago through yesterday (about 20 MB).
- **If EMA filters Production only by Service Date:** the last 13 weeks of service every Monday (about 50 MB). On the first Monday of each month, also the current service year, plus last year from January to March, zipped **[O1]**.

**Rules for both:**
- Never filter by payer, provider, location or biller.
- Don't open and re-save the CSV in Excel. It has already turned 2 check numbers into scientific notation.
- Put the files in the Dropbox folder (any subfolder of Trizetto Data) or on the server's Upload page. Overwriting last week's file is fine.
- Keep sending the three weekly exports until the parity weeks pass **[O7]**.

## 14. Owner decisions (defaults in brackets)

| # | Decision | Default |
|---|---|---|
| O1 | Production filter: posted date weekly (preferred; needs EMA to offer it), or service date (13 weeks) plus monthly year files | The code takes either; the guide gives the posted-date routine first and the fallback |
| O2 | Vercel: pass the gate (Step 4), or keep it on `ar` or retire it | `ar` until the gate passes |
| O3 | Confirm "Optum Unitedhealth Care" is VA community care, renamed in EMA in Sep 2025 (166 of 236 VA CCN patients are also paid under that name), before aliasing it. **Urgent:** until then those claims carry commercial windows (365-day appeal) instead of VA's 90 days | not aliased |
| O4 | Close items on EMA payments | on, as today (`REMITWATCH_EMA_CLOSES=1`); 0 makes C6 hints only |
| O5 | Billing-note text | never stored; the owner may ask to keep the latest note, shown only with names on |
| O6 | MRN | key only; stored and shown with names on only if the owner asks (useful for patient refunds) |
| O7 | Weekly routine: after four weekly pulls that meet the acceptance figures, drop ERA & Payment Lines and Claim & Claim Status, keeping Encounter & Charge Lines (Production carries the write-offs, the A/R the statuses) | keep all three until then |
| O8 | Compliance: who acts on Medicare credits over 60 days and on patient write-offs on insured lines (QMB, routine waivers) | RemitWatch only lists them |
| O9 | ASC facility claims: itemize EMA-only ASC denials; ask TriZetto about the ASC's 835s | yes, labelled "ASC facility claim" |
| O10 | Automation (Step 9): EMA's own scheduled delivery, or a robot; and a machine upload token | not built yet |

## 15. Acceptance on the real files, and clean-up
Aggregate-only scripts live in `/tmp/claude-0/-home-user-Billing-Optimizer/96e81489-2ad2-5c70-991d-e00db2e22749/scratchpad/arcoll/accept/`, run with `python3 -I -B`, and are never in the repo. Run them on copies of the live stores after each step.

| Step | Measure | Expected |
|---|---|---|
| 1 | A/R read | 9,653 rows; 4,229 + 61 claim ids; $1,115,513 open (2026) |
| 2 | NOT_SENT | 65 lines ($9.7K), 30 over 30 days; 43 NO_ANSWER items moved |
| 2 | Build 2 from the A/R, past p95 | about 90 primary claims ($54.1K); 61 secondary rows ($2.2K) |
| 2 | Credits | $37.1K; Medicare primary about $0; patient credits over 60 days: 71 rows ($4.8K) |
| 3 | Map | ≥ 95% of covered 2025+ bills (measured 98%) |
| 3 | Lean lines | about 74.5K of 204K |
| 4 | Vercel gate | peak < 1.75 GB; apply run < 120 s locally |
| 5 | C6 | about 259 closures ($28.3K); 137 + 109 hints |
| 5 | C2 | about 901 items ($83K) inside the deadline, from 17 |
| 5 | C3e | NO_ANSWER down by about 78-99 of the 124 checkable |
| 6 | C1 | $2.74M ± 2% (Jan 2025-Sep 2026); each cause within 5% |
| 6 | C4 | about 232 traces ($43.8K) over 20 months; fewer inside 365 days |
| 7 | C5 | about 460 lines (about $41K) inside the window |
| 7 | C7 | $459K in charges, split as in C7 |
| 8 | W4 | buckets add up exactly to the insurance A/R total |

**After the build,** delete these PHI-derived scratch files:
- `arcoll/raw/`
- `arcoll/ema_reports.sqlite`
- `arcoll/sizing/work.sqlite`
- `arcoll/sizing/map.sqlite`
- `arcoll/sizing/.salt`
- `arcoll/sizing/det/store.sqlite`