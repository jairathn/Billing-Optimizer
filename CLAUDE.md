# RemitWatch: notes for Claude Code (and anyone working on this repository)

RemitWatch reads a dermatology practice's payer remittances (835 files, from Dropbox or straight from TriZetto) and the
reports the practice exports from its practice-management system (ModMed EMA: the weekly exports, the A/R report and the
Production report), and turns them into a worklist: claims that are losing money and can still be recovered, each with
its evidence, its verdict and its exact next step, and claims that look open but were already worked or settled, which
must not cost anyone time. It runs itself every day on two hosts from the same code: a server (AWS, Docker, store on
disk) and a serverless site (Vercel, store image in a private Dropbox folder). The billers and the practice manager use
it in a browser; the software is meant to become the source of truth, so a silent mistake is the worst kind.

Read this file first, then docs/TROUBLESHOOTING.md when something is wrong. README.md and ARCHITECTURE.md describe the
product and the design in full; docs/OPERATIONS.md, docs/SERVER_RUNBOOK.md and docs/VERCEL_SETUP.md run the hosts.

## The owner's rules (non-negotiable)

1. Every dollar is accounted for. A line's money and the practice's money reconcile to the cent every run (charge =
   insurer paid + patient paid + contractual + written off + open, plus or minus credits). A dollar that does not
   reconcile becomes a visible gap with an amount, never a silent drop.
2. Every write-off has an owner. Money written off with no person's decision and no owner-approved policy is counted
   every week by payer and reason, with what is still recoverable.
3. Blindness is loud. When a feed is stale (835s, EMA reports), every page says in dollars what the app cannot check.
4. Work is ranked by money: dollars times the payer's historical recovery rate for the pattern, with the basis shown,
   then the deadline.
5. A scoreboard: recovered, lost to deadlines, at risk in the next 14 days, given up without a decision, week by week.

And: every verdict carries its proof (dated evidence reconciled to the cent and the rule that decided it); a person
can hand-check any item in under two minutes; detection, outcomes, states, closures and audits are rules only (AI only
reviews a visit note for an appeal, reads an incoming fax, or drafts a template on request, and always waits for a
person); nothing is closed by EMA's claim status alone; a person's decision is never overwritten by an automatic one.

## Privacy (hard rules)

- Patient data never leaves the store and the state folder: not into the repository, a test, a log line, an error
  message, a manifest, a diagnostic bundle, a screenshot, an issue or a pull request. Names, birth dates, record
  numbers, member ids, claim ids, check numbers, claim links, note text and exact dates of one claim are patient data.
- Logs and bundles use finding ids (24 hex characters), the store's own keys (line, claim and transaction keys made
  from file hashes), payer groups, codes, counts and dollars. Every log writer goes through the scrubber in
  remitwatch/runlog.py (Scrubber, scrub, scrub_text); never write a log line, an exception message or an event detail
  any other way. Never put a value read from a file into an exception message. A key does not make free text safe:
  only keys with a strict shape (timestamps, run and finding ids, the store's keys, codes, versions, repair targets:
  runlog.KEY_SHAPES) keep a value as it is; under any other key (status, source, how, rule...) it is scrubbed like any
  text. The scrubber learns the names the store holds, two-letter ones too (Le, Ng, Wu), and a name made only of words
  the code uses as well (PRICE, WILL) as its pairs of words; a word of the code's own beside a name it holds is masked.
- Tests use invented data only (tests/ema_fixtures.py, tests/fakes.py, the builders in tests/test_features.py and
  tests/test_remitwatch.py). tests/test_runlog.py plants invented names, birth dates, member ids and record numbers in
  a full cycle on each host and checks that no byte of any log, manifest or bundle holds them; keep it green.
- The repository is public: no personal names, no practice name, no TriZetto Site ID, tax ID, NPI, claim id or check
  number, no address of the live sites or of the practice's EMA, no payer alias specific to the practice
  (tests/test_docs.py scans for these by shape).

## Map of the code (remitwatch/)

Feeds and readers
- dropbox_sync.py: the Dropbox client and the router that sends each new file to its reader. trizetto.py: 835s straight
  from TriZetto's SFTP. uploads.py, api_uploads.py, intake.py: files people upload in the app (chunked, resumable) and
  the hand-off to the cycle. x12_835.py, x12_837.py: the X12 parsers. ema_exports.py: EMA's weekly exports.
  ema_reports.py: EMA's A/R and Production reports (recognition, streaming readers, keyed hashes for names, the report
  catalog). ema_ledger.py: the Production postings ledger and the summaries the store keeps. link.py: 835 lines to EMA rows.
The store
- store.py: the SQLite store (a file on a server; a compact in-memory image on Vercel). state.py: the serverless state
  folder (LocalState for tests, DropboxState live). cloud.py: the serverless cycle, the store image, the dashboard's copy
  (serving.sqlite.gz), the journal of people's actions, locks and budgets.
Analysis (rules only)
- detect.py: the detection run, in the order of the design's amendment 4.5; its save keeps people's work. archive_rules.py:
  what the 835 archive alone proves. ema_checks.py: what EMA's reports add. states.py: the one place an item's state is
  decided (work now, waiting, done, accepted, expired, can't check). story.py: each item's story, verdict and next step.
  codes.py, fees.py, pfs.py, mpfs.py, ascfs.py, ascrates.py: reference tables. alerts.py, insights.py, report.py: alerts, the
  home screen, the weekly summary. activity.py, calculator.py: the owner's provider and practice figures. results.py:
  the Results and ROI tab.
Running itself
- pipeline.py: the server's cycle and scheduler. cloud.py: the serverless cycle. selfcheck.py: self-regulation, the
  per-run invariants with safe mode, the weekly self-audit, the track record, the pattern watch, the money checks.
  archive.py: retention and the cold archive. runlog.py: run manifests, the event log, the decision log, the scrubber,
  log retention. repair.py: the self-repair registry. diagnose.py: the diagnostic bundle. runtime.py: which host this is.
People's actions and the site
- api.py (and api_*.py routers): the web app. actions.py: what a person records, applied to any copy of the store.
  appeals.py, notes.py, modmed.py, fax.py, lessons.py: appeal packets, visit notes, faxes. static/: the pages.
- cli.py: the command line (`python -m remitwatch.cli --help`).

## The daily cycle

Server (AWS): the scheduler in the web process starts `python -m remitwatch.cli cycle` in a child process (every
REMITWATCH_SYNC_INTERVAL_MINUTES, on a Dropbox webhook, or from Sync now), under the pipeline lock (a meta row):
uploads from the intake folder, the Dropbox delta, TriZetto, the reports waiting to be read again, the ledger refresh,
detection (when anything changed or the rules changed), alerts, mail, the daily backup and the fax run.

Serverless (Vercel): no daily analysis (the owner's decision 1). A run starts only when someone presses "Run the
analysis now" (the Data tab), which calls /api/cron/cycle; vercel.json's only cron is the fax line's (15:43 UTC). One run
takes lock.json in the state folder, loads the store image into memory (2 GB function, 300 s), folds in the journal,
reads the intake folder, TriZetto (only when switched on there) and the Dropbox delta (EMA's A/R report only: Production
waits for the server, REMITWATCH_EMA_REPORTS), then detection (or hands it to a fresh invocation), saves the store, then
the dashboard's copy, mails, writes status.json and releases the lock. A run that has more to do starts the next one
itself (at most 30 in a row), and once the chain has analysed, one more run does what is due of the archive pass, the
provider tables and the weekly self-audit (cloud.tidy_due) and analyses nothing.

Both hosts write the run's logs (remitwatch.runlog), keep the decision log of every automatic change to an item, run
the self-checks (remitwatch.selfcheck) and the self-repair rules (remitwatch.repair). The software repairs operational
failures by fixed rules (a network error is tried again, a file that cannot be read is held, a derived table that fails
its check is rebuilt once, a lock left by a dead run is removed). It never edits its own code: a code bug is contained
(safe mode keeps the last good worklist and holds automatic closures) and turned into a diagnostic bundle.

## Logs, decisions and the diagnostic bundle

- Server: /data/logs (beside the store): events-YYYY-MM-DD.jsonl written line by line, runs-YYYY-MM-DD.jsonl (one
  manifest per run), gzipped after the day, kept 180 days. Vercel: logs/ in the state folder, one gzipped file per run,
  kept 90 days; Vercel's own function log gets one line per run (run id and outcome).
- The store keeps run_log (a short copy of each manifest), item_decisions (every automatic change to an item with its
  rule, rule version, evidence keys, and before and after: the drawer's "Why did this change?") and repair_log.
- The bundle: `python -m remitwatch.diagnose --days 7` on a server (or `--state-dir <copy>` for a copy of Vercel's
  state folder), or the button in the Data tab's "Logs and diagnostics" panel. docs/TROUBLESHOOTING.md says how to read it.

## Working on a problem

1. Read the bundle (README.md inside it): the first error of the first failing run, its `where` and stack, the steps'
   time and memory, the failing self-checks and their example items with their decision logs.
2. Reproduce on a copy, never on the live store (REPRODUCE.md in the bundle; docs/TROUBLESHOOTING.md).
3. Write a test that fails for the reason found, with invented data, on both store kinds where the code runs on both
   (the server's file store and the compact in-memory image cloud.py builds; see tests/test_cloud.py and
   tests/test_runlog.py's compact_store).
4. Fix it. Break the fix on purpose and see the test fail, then restore it exactly (`git diff`).
5. Run the suite in the four environments (below) and the two node checks when pages changed.

## Tests: four environments, all must pass

- `python3 -m pytest -q`: the newest Python with a recent SQLite (3.5x).
- The same with the newest libraries (a fresh virtual environment with `pip install --upgrade -r requirements-dev.txt`):
  FastAPI and Starlette change behaviour between versions.
- Python 3.12 with SQLite 3.45: what the AWS image (python:3.12-slim) and CI run. SQL must not name an outer table
  inside a subquery's ORDER BY.
- Python 3.11 with SQLite 3.40.0: Vercel's SQLite (`uv python install 3.11.1`; that build carries SQLite 3.40.0). No SQL
  function newer than 3.40 (unhex, octet_length, timediff, concat, concat_ws, string_agg, ORDER BY inside an aggregate,
  jsonb_*, IF() as a function). Only tests that serialize a store image may fail there (no Connection.serialize in
  that build; Vercel has it).
- `node tests/calc_model_check.js` and `node tests/lab_review_check.js` when static/ changed.
Every bug gets a failing test first. Tests use invented data and cover edge cases: empty, duplicate, malformed, huge,
unicode, leap-day and year-end dates, reversals, negative and zero amounts, two people acting at once, both hosts.

## Releasing

Work on a branch, never on main. Run the full suite in the four environments and the node checks on a clean checkout.
Pushing to main deploys both sites: Vercel builds main, and the server's automatic deploy pulls main within minutes.
Then check /api/health on both sites: each shows the commit it runs, and both must show the one pushed. Watch the next
run's status (Vercel: status.json in the state folder; the server: the Data tab) and its logs.

## Never

- Edit the live store, the state folder or the ledger by hand. Work on a copy.
- Run a copy of the store with the live Dropbox, TriZetto, fax line or mail switched on (REPRODUCE.md switches them off).
- Push patient data anywhere (code, tests, docs, logs, issues, screenshots) or a secret.
- Skip a test environment, mark a failing test to be skipped, or delete a test to make the suite pass.
- Catch an exception without logging it (`runlog.caught(exc, step)` in the except block), or close an item without
  its proof rows.
- Retry anything but a read. remitwatch/repair.py tries network calls again (retry_call, resilient); a callback that
  runs inside a retried call (trizetto.pull's ingest) goes in `repair.not_retried(...)`, so a code bug in it is raised
  at once instead of repeating the whole call. A send, an upload, a move or a delete is never wrapped.
- Let AI decide a state, an outcome or a closure.

## House style

Pages and docs: plain English, short sentences, no em dashes, no jargon ("false positive"): say what happened and what
to do. Code comments say why. New settings are named in .env.example and in the runbooks (tests/test_docs.py checks).
