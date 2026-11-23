# RemitWatch

RemitWatch reads the practice's own payer remittances (X12 835 files from TriZetto), claim files and ModMed EMA exports, links what was paid to what was billed, and shows exactly where money is being lost: downcoding, bundled-away visits, silent line denials inside "paid" claims, whole-claim denials that were never worked, allowed amounts below the payer's own rate or below Medicare, multiple-procedure reductions beyond policy, payment fees and takebacks. Every finding carries the evidence, a filing date with its source, and a ready-to-send letter.

It is a living site. While it runs it pulls new files from the `Trizetto Data` Dropbox folder on a schedule (and within a minute of an upload when the Dropbox webhook is set), re-runs detection, raises alerts when a payer changes behavior, and records every payment that arrives for a line that was first denied or cut.

Built for a dermatology practice. Everything is practice-agnostic except the payer rules in `remitwatch/codes.py`; the practice's own name, address, tax ID and NPI for the letters are settings (`PRACTICE_*`).

## What you get

| | |
|---|---|
| Today | The page it opens on: a numbered plan for the day (what closes this week, follow-ups on what was sent, the biggest batch, items with no filing date that someone has to decide), batches of one kind of work for one payer, what changed in payer behaviour, and a first-visit guide. A banner says when the data cannot be trusted yet (a stale feed, missing remittance weeks) |
| Denials, Bundling & modifiers, Downcoding, Contracts & underpayment | One tab per category, right after Today, each on the same short page: boxes that filter the list (closes in 7 days, window open, to decide, sent, window closed), one line per kind of work with what to do and the payer with the most of it, at most two fix-once notes (a cause that keeps coming back, a payer change, the January 2027 changes; on Contracts, the payers paying below Medicare), then the items 20 at a time. The red number on a tab is what closes there within 7 days. The charts, the table by reason, what each label means and, on Downcoding and Contracts, the clinician and rate tables are in one closed Details fold |
| Fees & takebacks | Takebacks of the last 90 days to check against the payer's letter, each with its dispute-by date and a link to the claim it was taken from (every takeback is one click away), and payment fees by payer as the remittance names it: the last 12 full months and since the first fee. Each payer opens its own letter asking for direct deposit with no fee, with its totals; "Mark asked" moves the payer to Asked, where its letter can be printed again or the request put back. What the vendors took stays in every total |
| Find a claim | The box at the top takes any claim ID (any submission of the bill, or the start of the ID) or the payer's claim number however it is typed, whatever its status or filing window, and with names shown a patient's name or member number. It opens the whole bill: every submission and every remittance for it in the payer's order, each with when it was received, the service dates, whether it was an original or a correction, the clinician, the payer it was sent on to and the payer's phone, and every line, adjustment and remark in plain words; the items found on it (its own, a takeback that names it, a payer's routine lower rate it is part of), with the line each was found on; the money a payer took back naming it; and the EMA rows, also of a submission no payer has answered yet. "Export every line" saves all of it. Each item's drawer links to it, a takeback's to the claim it was taken from |
| Calculator (owner only) | What a clinician's year (or the practice's average Mohs surgeon, general dermatologist or APP) brings in at each payer's own rates. The schedule is set in the terms a clinician plans in (clinic days, Mohs cases, excisions, visits, patients biopsied, AK and benign patients) with the clinician's own code mix; a waterfall separates the collections now, Medicare's 2027 amounts and rules (a policy cut nobody chooses), the schedule and corrected contracts (whole contract, visit codes only, or a floor, at a target share of Medicare). It reads the last six complete months by default and keeps the practice's ancillary and product revenue (radiation, skin-substitute products, pathology, drugs) apart from the clinician's own production. Beside it: a Strategy lab (stackable strategies, each with collections, costs, visits, clinician minutes and a compliance tier, including where closures after clinic Mohs are done: clinic or the practice's ASC), Practice figures (minutes, costs, pay plans, ASC ownership and cost; owner password required), a printable Provider sheet (own production only), a Coding exposure panel, and an ASC final rule view: what Medicare's 2027 ASC final rule does to the facility fees for the cases the surgeons bill at the practice's ASC (each kind of payer at what it pays the practice against Medicare, which can be changed), with the same cases' surgeon fees, and Medicare's ASC rates built in (CMS's own files, worked out as ASCA's calculator works them: any code, any county, to the cent) |
| Worklist | Every item from the four claim tabs in one list, on the same page. Opening one says what happened, what to do, what you need and the date it must be done by; "Mark sent and open the next" walks the list (an item someone has to decide also offers "Let go and open the next"), the claim's details, the note-based appeal builder and the technical detail are folded below. Statuses: To do, Started, Sent, Paid (set by itself when a later remittance pays), Lost, Let go |
| Bigger wins | Money fixed once and collected on every later claim: each payer's rates against Medicare, what changes on January 1, 2027, denial causes that keep coming back and who fixes them, fees set below what a payer allows, payment-vendor fees. Each panel leads with its next step; the explanation is behind "Why" |
| Data | A checklist of what makes the numbers untrustworthy and who fixes it, then the feed, the missing remittance weeks and the EMA weeks the charge exports miss |
| Providers | What each clinician is doing, read from the same remittances: clinic days, patients seen per clinic day and per week (by office state where the practice works in more than one), new patients, the size of each panel and how many of last year's patients came back (established patients apart from first-time ones), visit levels on visits with no procedure, biopsy rates within Medicare and within everyone else, how often a first biopsy led to a skin cancer treated here, what a visit is allowed, and how many patients treated for a skin cancer have not been seen for a year. A second panel covers recent weeks from the EMA charge exports and will not count a day until that clinician's bills for it can exist. Weeks in which a payer's remittances went missing are flagged. The tab can be kept for the owner behind a second password typed into it (`REMITWATCH_OWNER_PASSWORD`) |
| Who and what | Any finding family broken down by provider, payer, plan, billed code, billed-to-paid pair, modifier, place of service, location, diagnosis, month and more, one or two at a time, with the number of lines billed beside each count so a rate is always shown |
| Alerts | Ramp, slow climb, restart after a quiet period, new behavior, one provider singled out, rate cuts, new reason codes, deadlines, stopped feed. By email and optional webhook, with no patient data |
| Progress | Statuses (open, in progress, appealed, recovered, lost), recovered dollars by month, share recovered by payer, downcoded visits per 100 by payer over time |
| Appeal packets | Template letter for every signal with verified statute wording; or upload the visit note and get a reviewed packet (letter, documentation review with quotes, remittance evidence, the note) as one PDF to download or email. With ModMed EMA connected, the note is read from EMA itself: one button in the item, or a batch from the Worklist |
| Appeals by fax | Tick items on any list and press Appeal by fax, or press Appeal everything appealable: for each item the note is read from EMA, Claude writes the paragraphs that justify the claim from it, and the letter, note and evidence are faxed to the payer behind a cover sheet; the item is marked Sent. What cannot go is held with the reason (among them a review that has doubts, or quotes the note does not have: those are never faxed by themselves). Only numbers a person saved are used, and a changed number waits for a person to confirm it. One site sends (the server, `REMITWATCH_FAX_SENDER`), each fax is written down before it goes, and fax runs never overlap, so nothing goes twice. Each server cycle (or a daily run on Vercel) reads what payers fax back (Claude reads each fax; it is tied to its item by itself only on a claim number, from a number known for that payer), sends again what a busy line did not take, and follows up by itself: a second request once the payer's time to answer has passed and then every week, the next level when a denial is upheld, the records when asked, a payment request when an approval is not paid. The Faxes page shows the payer numbers, what went out and what came in |
| Always current | Remittances read straight from TriZetto's SFTP on every run (no Dropbox copying), a run started when the site is opened on old data, items EMA shows paid closed by themselves (a corrected claim paid under a new number, a paper check), and no appeal, second request or payment request faxed while the remittances are more than two weeks behind |
| Money nobody is working | Items whose disputed money the biller wrote off in EMA while the payer can still be asked for it, and claims EMA shows sent and unpaid that no payer ever answered (or that were rejected before reaching it), each with the date it must be dealt with by: two steps on Today's plan and a filter and a kind of work on the Worklist |
| Hide a reason for a payer | One click takes a reason a payer never pays (a modifier 57 visit at some payers) off every list, now and after each analysis; "Show again" undoes it |
| Suggested templates | Bigger wins lists, by code, what Claude's reviews found missing from notes that could not be appealed, next to what the note for that kind of service must carry, and writes the EMA template fields to require so the next note carries them before it is signed |
| Upload from EMA | On the server, until ModMed's API is approved: EMA's three weekly exports, remittance files and visit notes dropped on one page go through the same loader and the same note matching as the automated feeds. Each upload says what it held, what was new, what was already there and what was left out and why; the same file twice changes nothing; a guide on the page says which reports to run, with which columns, for which dates and how often |
| Hands-off | One cycle does sync, ingest, detect, alerts and notify. It cannot overlap itself and needs no attention |

## What it found (the archive through October 9, 2026)

See `docs/DATA_FINDINGS.md` (regenerated by `scripts/leak_report.py`). About $2.40M of detectable leakage is still open across 2024 to 2026. Visit downcoding is real and small: 229 of 93,878 office visits, about $7.7K, nearly all from two Anthem product lines, with one provider carrying 44 percent of it. The large items are contract rates below Medicare ($897K at Medicare parity, mostly Anthem commercial visits at 59 to 72 percent of Medicare), denials never worked ($633K), fees and takebacks ($531K), bundled-away visits and procedures ($162K) and underpayment against the payer's own rate ($128K). A further $1.16M across 5,574 lines was denied or bundled at first and paid on a later remittance; those are marked recovered automatically. About $225K is open and still inside its filing window (November 2026, after the fixes from the sixth review).

## Signals

| category | signal | meaning | action |
|---|---|---|---|
| downcoding | DOWNCODE_SVC06 | payer reported the billed code (SVC06) and paid a lower level | appeal |
| downcoding | DOWNCODE_VS_BILLED | the claim file or EMA shows a higher level was billed than the remittance admits | appeal |
| downcoding | DOWNCODE_CHARGE | charge equals the practice's fee for a higher level than the paid code | appeal |
| downcoding | DOWNCODE_LEVEL_REDUCED | reason code 186 or 150 on a paid visit: payment cut for the level, billed code left in place | appeal |
| downcoding | DOWNCODE_RATE | allowed equals the payer's usual rate for a lower level | appeal |
| downcoding | DOWNCODE_LINE_SWAP | billed visit denied and a lower-level line paid in its place | appeal |
| downcoding | DOWNCODE_LEVEL_DENIED | visit paid $0 with reason code 150/186 or a level remark | appeal |
| downcoding | CODE_CHANGED | payer changed a procedure code (SVC06 or remark N22) | review |
| bundling | EM_BUNDLED_MOD25 / MOD24 / MOD57_MAJOR | E/M paid $0 as included in a procedure | appeal |
| bundling | EM_BUNDLED_NO_MODIFIER / MOD57_MINOR | E/M bundled because the modifier was missing or wrong | correct and resubmit |
| bundling | ADDON_BUNDLED, PROCEDURE_BUNDLED | add-on or procedure bundled into another service | appeal |
| denial | DENIED_MISSING_MODIFIER, DENIED_CODING, DENIED_MISSING_INFO | correctable errors (CO-16/4 with RARC) | correct and resubmit |
| denial | DENIED_AUTH, DENIED_MEDICAL_NECESSITY, DENIED_LEVEL_OF_SERVICE, DENIED_FREQUENCY | payer judgment calls | appeal with records |
| denial | DENIED_TIMELY_FILING, DENIED_PROVIDER, DENIED_NONCOVERED, DENIED_DUPLICATE_UNPAID, DENIED_OTHER | process failures | review |
| denial | CLAIM_DENIED_UNPAID | whole claim denied and still unpaid, one finding per bill (lines paid elsewhere on the bill are left out) | appeal or resubmit |
| underpayment | UNDERPAID_VS_OWN_RATE | allowed below the payer's own usual rate for the code and provider type, no reason reported | appeal |
| underpayment | SECOND_RATE | the payer allowed the same lower amount for a code on five or more lines and the remittances support a reason (PA or NP rate, an office in another state, one plan, multiple-procedure rule, a rate change): one finding per payer, code, year and amount, with its claims, each with its own filing deadline | one question to the payer |
| underpayment | MPPR_EXCESS | multiple-procedure reduction beyond policy or on an add-on | appeal |
| fees | FEE_SKIM | Zelis transaction fees, origination fees withheld (PLB CS/AH) | opt out |
| fees | TAKEBACK | overpayment recovery withheld (PLB WO) | review |
| contract | CONTRACT_RATE_GAP | commercial rate far below Medicare on a high-volume code | renegotiate |
| contract | MA_BELOW_MEDICARE | Medicare Advantage rate below Medicare | review contract |
| contract | CHARGE_BELOW_RATE | the practice's charge for a code is below what the payer allows, so the payer paid the charge | raise the fee |
| contract | MPPR_POLICY | payer routinely allows later same-day procedures below 50 percent | review contract |
| info | G2211_DENIED, MIPS_ADJUSTMENT, SEQUESTRATION | context | none |

## Quick start (local)

```bash
pip install -r requirements-dev.txt
```

```bash
python -m pytest -q
```

```bash
python -m remitwatch.cli --db data/remitwatch.sqlite cycle --skip-sync --local "data/dropbox/Trizetto Data" --no-email
```

```bash
REMITWATCH_PASSWORD=a-long-local-password python -m remitwatch.cli --db data/remitwatch.sqlite serve
```

The third command ingests every remittance, claim file and EMA export under the folder, runs detection and the alert checks. The fourth serves the dashboard on http://127.0.0.1:8000. Without a password the dashboard serves no data, as on the live sites, unless `REMITWATCH_ALLOW_OPEN=1` says this is your own machine (never set it on a server or on Vercel). A password shorter than 12 characters works on your machine and on a server, with a warning in the log; Vercel refuses it.

## Running it for real

```bash
python -m remitwatch.cli oauth
```

```bash
docker compose up -d
```

The first command prints the Dropbox refresh token for `.env` (once). The second starts the dashboard, its scheduler and HTTPS. Hosting, the webhook, alerts, the note review and the agreements that have to be in place are in `docs/OPERATIONS.md`.

### On AWS

`docs/AWS_SETUP.md`: one template builds the server (encrypted disk, HTTPS only, daily backups, nightly system updates),
one script takes the settings and moves the Vercel site's data over. AWS's business associate agreement is free. A merge
to main deploys itself within about 5 minutes (`deploy/auto-deploy.sh`, run from a copy the installer keeps on the
server; `docs/SERVER_RUNBOOK.md`), and `/api/health` on either site names the commit it runs. Whoever can push to main
controls the server, so protect main (`docs/SERVER_RUNBOOK.md`, "Who controls the server").

### On Vercel

A push to `main` deploys the site on Vercel. Vercel keeps no files and no running process, so there the store lives in a private folder of the practice's Dropbox and each update is one time-boxed run (`remitwatch/cloud.py`). Add the settings in Vercel, open `/setup` on the site, and follow its checklist: it checks every setting live, gets the Dropbox token, and starts the first load. While the practice's server runs, the server alone faxes and reads TriZetto: the Vercel site does neither unless told to (`REMITWATCH_FAX_SENDER`, `TRIZETTO_SFTP_SERVERLESS`). The settings, the limits (memory above all) and how it works are in `docs/VERCEL_SETUP.md`.

## Layout

```
remitwatch/
  x12_835.py      835 parser (all loops, balancing, content fingerprint for duplicate detection)
  x12_837.py      837P reader (what was submitted)
  ema_exports.py  ModMed EMA export loaders, Excel or CSV (charge lines, ERA lines, claim status)
  uploads.py      files brought in by hand on the Upload page (a server): exports and remittances queued for the cycle, visit notes matched to their visits
  store.py        SQLite store, idempotent ingestion
  link.py         835 line to EMA charge line and biller handling
  fees.py         fee-schedule inference from charges; code level families
  codes.py        reason and remark texts, global periods, payer rules, filing windows, statute wording
  detect.py       detection engine: baselines, findings, denominators, status carry-over, claims no payer answered
  insights.py     what Today and Bigger wins say: kinds of work, the plan, the data checks (the TriZetto feed's among them)
  activity.py     what each clinician did: who rendered each visit, patients per clinic day, panels, levels, allowed per patient
  calculator.py   the collections calculator's figures, and the ASC final rule view's (asc_rule)
  pfs.py          global periods and multiple-procedure indicators from CMS's physician fee schedule file (generated)
  mpfs.py, ascfs.py  Medicare physician fee schedule and ASC payment tables for the dermatology codes (generated)
  ascrates.py     Medicare's ASC rates (CMS's addenda, worked as ASCA's calculator works them): every code, every county, both years (tables in cms/, generated by scripts/build_ascfs.py from CMS's own files)
  alerts.py       what changed since the last look
  appeals.py      letters and correction instructions
  notes.py        visit-note review and appeal packet (PDF, email)
  modmed.py       ModMed EMA through its certified FHIR API: the visit note for a claim (signed sign-in, search or Bulk FHIR export; the patient by two identifiers)
  fax.py          the fax line (SRFax): one sending site, cover sheets, follow-ups, payers' faxes read and tied to their items
  lessons.py      suggested templates: what the note reviews found missing, by code, and the EMA template Claude writes from it
  trizetto.py     remittances read straight from TriZetto's SFTP server (read only; a refused password is held for a day)
  pipeline.py     the cycle, its lock and the scheduler (a host with a disk)
  cloud.py        the same cycle for a serverless host: store in memory, saved to a state folder, time-boxed runs
  state.py        the state folder (Dropbox, or a local directory for tests)
  actions.py      status changes and acknowledgements, applied to the store or written to the journal
  runtime.py      which of the two modes is in effect, and what the host offers
  dropbox_sync.py Dropbox client (refresh token, cursor deltas, downloads, uploads)
  report.py       summary report and email
  api.py          FastAPI endpoints
  static/         dashboard (index.html, today.js, desk.js, features.js, claim.js, faxes.js, providers.js, calc*.js, lab*.js, asc*.js), the setup checklist (setup.html) and the Upload page (upload.html); no build step
  cli.py          cycle | weekly | sync | ingest-835 | ingest-ema | link | detect | activity | alerts | stats | serve | oauth | modmed-key | modmed-check | prepare-appeals | send-appeals | fax-cycle | import-cloud | compare-cloud | accept-store | backup
api/index.py      Vercel entry point
vercel.json       one function behind every address, two daily schedules (the cycle at 10:17 UTC, the fax line at 15:43 UTC)
public/           robots.txt only: Vercel serves nothing else from the repository
Dockerfile, docker-compose.yml  the server's image (it keeps the commit it was built from) and its two services (the app, Caddy)
scripts/          build_mpfs.py and build_ascfs.py (CMS tables), leak_report.py (docs/DATA_FINDINGS.md), cloud_bench.py
docs/             operations, the server runbook, AWS and Vercel setup, the TriZetto request, findings, missing data, research summary and raw research notes
deploy/           container entrypoint, Caddyfile, cron example (only for a host that does not keep the dashboard running), the AWS template and settings script, auto-deploy (script, installer, systemd timer)
tests/            synthetic-fixture tests (no patient data); stand-ins: fakes.py (Dropbox), fake_sftp.py (TriZetto), fake_fax.py (SRFax), fake_modmed.py (ModMed's sign-in and FHIR API)
```

## PHI

The store, the Dropbox mirror, the uploads, the backups and the appeal packets contain patient names, member numbers and dates of service. `data/` is git-ignored. Every site serves no data until `REMITWATCH_PASSWORD` is set; run it behind HTTPS with the shared login and keep the host under a BAA. With `REMITWATCH_SHOW_PHI` off (the default) the screens show initials and placeholders, the CSV export has no patient column, and each finding carries the claim ID to look the patient up in EMA. With `REMITWATCH_SHOW_PHI=1` the work queue, the search box, the claim detail and the CSV export show the patient's name and member number to everyone who can sign in. Off is a setting for the screens, not a barrier: the packet PDF carries the name and member number for every login, because the payer needs them, and dates of service and claim numbers are served either way. So the long random password and the BAA are needed in both settings.

Where patient data goes, and under which agreement:

- Anthropic (the practice's BAA, signed November 2026): the visit-note review sends the note's text, with the identifiers known from the remittance and common identifier patterns removed (`REMITWATCH_NOTE_SCRUB=1`, the default); a scanned note has no text to clean and is sent only with `REMITWATCH_NOTE_SEND_SCANS=1`. Every fax a payer sends back goes to Claude whole, as a PDF, to be read: it carries the patient's name, member and claim numbers and dates. "Write the EMA template" sends only codes, payers and what notes lacked.
- SRFax (its own BAA, signed before the `SRFAX_` settings are added): each appeal packet faxed carries the letter with the patient's name and member number, the visit note and the remittance evidence; the cover sheet names no patient. Payers' faxes come back through it. Only numbers a person saved are used.
- ModMed EMA: visit notes are read from its FHIR API (read only); a note stays in memory or in the appeal packet built from it.
- Email: an appeal packet goes only to an address in `APPEAL_EMAIL_DOMAINS`, through the practice's mail service (under its BAA). Alert and summary emails, the chat webhook (`ALERT_WEBHOOK_URL`) and the deploy's failure messages never contain patient data.
- Dropbox (a BAA on its team plans): the remittances and EMA exports, and on Vercel the whole store, the packets and the journal in the state folder.
- On the server, files uploaded by hand stay in `/data/uploads` (owner-only) and the daily copies of the store in `/data/backups` (owner-only), both on the encrypted disk and in its daily snapshots. An export or remittance file never leaves the server; a visit note leaves it only inside its appeal packet and for the review.
