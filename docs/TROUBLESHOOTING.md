# Troubleshooting RemitWatch

For whoever has to find out why something went wrong, with Claude Code and this repository open. RemitWatch repairs
operational failures by itself, by fixed rules, and logs each repair. It never changes its own code: a code bug is
contained (safe mode keeps the last good worklist and holds automatic closures) and written up in a diagnostic bundle
that holds no patient data. Start with the bundle, reproduce on a copy, write a failing test, then fix.

## 1. Get the bundle

- In the app: the Data tab, "Logs and diagnostics", download the diagnostic bundle (the last 7 days of runs).
- On the server: `sudo docker compose exec app python -m remitwatch.diagnose --days 7` writes a zip in
  /data/diagnostics. Copy it out with `sudo docker compose cp app:/data/diagnostics/<file>.zip .`. One run only:
  add `--run <run id>`.
- From a local copy of Vercel's state folder (Dropbox, /RemitWatch-state): `python -m remitwatch.diagnose --state-dir <copy> --days 7`.
  When the runs stopped over the store image (it is not the one the last run saved, it is missing, or it is cut short),
  the bundle is still made: its README.md starts with "Read this first" and the reason, and bundle.json has it in `notes`.

The bundle holds finding ids, payer groups, codes, counts, dollars and the code's own file names and line numbers,
never a patient's name, birth date, member id, record number, claim id or check number. Dates of single items are cut
to their month. It is safe to give to Claude Code.

## 2. Read the bundle

| part | what it answers |
|---|---|
| bundle.json | which runs it covers, the code commit and rules version, the versions of Python and SQLite, the scan of the bundle (`masked_on_second_pass`: what the second pass had to mask, 0 on a clean bundle; `left_out`: errors left out of errors.json) |
| errors.json | every error of those runs: `type`, `message` (scrubbed), `where` (file:line in function), `stack`, `step`, `contained` (true: the run went on; null: raised to the caller; false: it ended the run). Past 2,000 errors it keeps each run's first error and the newest ones, and `left_out` says how many it left out |
| runs/<run_id>.json | one run's manifest, whole: `outcome`, `steps` (each with ms, rss_mb, peak_mb, peak_rise_mb, /tmp on Vercel), `inputs` (files by hash, kind, origin, result), `positions` (Dropbox cursor and ledger revision, as fingerprints), `outputs` (items by state and signal), `invariants`, `repairs`, `sections.decisions`. A killed run's record has its `last_step`, its memory then and the steps it finished |
| events/<run_id>.jsonl | the run's event log, one JSON object per line, all of it: ts, run_id, step, level, event, rule, rule_version, item, counts, ms, mem_mb, detail, error (a killed run: its last 20 events) |
| invariants.json | the self-check results run by run, and the self-check's own history tables |
| examples/<check>.json | for each failing check: up to 20 example items, each with its decision log |
| repairs.json | what was repaired by itself, and the open problems with their next step |
| decisions.json | the automatic changes to items in the window, counted by kind and rule |
| store.json | every table with its columns, indexes and row counts; meta keys (values only where safe) |
| settings.json | the names of the settings in force (never their values) |

Order of attack:
1. The first failing run (`outcome` failed, partial, killed or safe_mode in bundle.json). Its first error is usually the
   cause; later errors are often consequences.
2. Its `where` and `stack` point at the line. The `step` says which part of the cycle it was in; the step's
   `inputs` name what it was working on (by id or hash).
3. A run whose outcome is "killed" was stopped by the platform and wrote nothing at its end. The next run found its open
   marker and wrote its record: runs/<run_id>.json (the step it was in, `last_step`, its memory then and the steps it
   finished) and events/<run_id>.jsonl (its last 20 events). The next run also logged `previous_run_killed`.
4. A run that failed before its run log started (a store that could not be opened) has a record with the error's stack
   and its `step` (open_store). A scheduled cycle that ended with an error code before its run log could say why is a
   run with an `exit_code`.
5. A failing self-check: its example items and their decisions show which rule changed them, in which run, from what
   to what. A check that started failing at one run points at what that run changed (its `inputs`, its
   `sections.decisions`, its commit).
6. Item changes nobody expected: decisions.json by rule, then an item's own decisions (the drawer's "Why did this
   change?", or `runlog.decisions_for(conn, finding_id)` on a copy).

## 3. Failure classes, what RemitWatch does about each, and what a person does

**A feed does not answer** (Dropbox, TriZetto or SRFax: a dropped connection, a timeout, HTTP 5xx or 429).
RemitWatch tries the call again after 2, 8 and 30 seconds within the run's time (remitwatch/repair.py, remedy
`network`), then holds that call and carries on with the rest. Later calls to the same service in that run get one
plain try each, with no waiting (a success ends the hold), and after three of those fail, none: an outage costs a run
one wait, not one per file. A run counts once however many of its calls were held, and a service held on three runs
in a row raises the alert "RemitWatch could not repair ... by itself". Only network trouble is tried again: a code bug
is raised at once whatever its message says (a TypeError about a `timeout` argument, a row number that holds "502"),
and so is anything the ingest raises inside a TriZetto pull. A refused token or password is never tried again
(TriZetto keeps its own hold for a day so the login is not locked), and a fax whose sending may have happened is never
sent again by a retry (the fax line's status checks and inbox reads are tried again: they send nothing). To do: run the
service's test on the setup page; fix the setting it names; the next run tries again by itself.

**A file cannot be read** (an export cut short, the wrong report, columns missing, too big for Vercel, too slow three
runs in a row). The reader refuses or holds it with the reason; the other files go on. The registry (remedy
`unreadable_file`) holds a file that failed twice, and a report the reader held, and raises an alert naming its kind
and hash. A refused report (the wrong file, columns missing) is not a hold there: the Data tab and the Upload page say
why, and only a new export helps. To do: open the Data tab, check the held file's reason, export it again from EMA (or
upload it in the app, which compresses it), and discard the held one. The alert clears by itself once the held file
is discarded or applied, or a later export of the same report and years is applied. If a fresh copy is held too, it
is a code bug: reproduce with an invented file of the same shape.

**A run was stopped** (Vercel's 2 GB memory or 300 second limit; a server container restarted). The run's open marker
(logs/open/) is written whenever a step starts (on Vercel also every 20 seconds within a long step), with the run's
memory and its last 20 events. The next run finds it, records the run as killed in the step it was in, and writes its
record (the bundle's runs/ and events/ have it). The cycle's own repair paths run (cloud.repair_ledger when the ledger
was saved and the store was not; a Production file whose read was started three times is held). To do: look at the
killed run's last step, its memory and its last events, and the steps before it in earlier runs; the self-regulation
(remitwatch/selfcheck.py) spreads the work over more runs when a run passes 80% of its budget. Two kills at the same
step in a row raise an alert; a later run that finishes and passes that step clears the count by itself.

**A cycle died before its run log started** (a store that cannot be opened or brought up to date, a full disk, a
damaged file). The cycle writes a failed run with the error's stack to the logs folder, which needs no store, and the
scheduler notes a cycle that ended with an error code. The Data tab's panel says so, and also says when no run has
started for twice the usual time between runs (the scheduler or the cron stopped). To do: the bundle's first error;
reproduce on a copy of the store.

**A lock left behind.** Server: a cycle lock whose process is gone is removed at the next start or after its child ends
(pipeline.clear_locks_left_behind, clear_lock_of_pid); a lock with no heartbeat for three hours is taken over. Vercel:
lock.json older than the time budget plus two minutes is taken over (cloud.acquire). Each removal is logged as remedy
`stale_lock`. To do: nothing, unless it happens every day (then look for the run that dies).

**A store missing a column** (a store from an older release). ensure_schema adds it at the start of the run; the repair
log records the columns before and after (remedy `schema`). To do: nothing, unless it repeats.

**A derived table fails its check** (the ledger's summaries against the store, the link map, the dashboard's copy, the
items). It is rebuilt once from its sources and checked again (remedy `derived_table`). If it still fails, the run asks
for safe mode: the worklist people see stays on the last good run and automatic closures are held, and an alert says
which check failed. To do: the bundle's examples/ for that check, then reproduce on a copy.

**Money posted in EMA left the posting ledger** (the self-check `posted_money`, critical: it holds the run). For each
service month, closed posting month, payer group and measure (insurer paid, patient paid, adjustments, patient
adjustments) the check keeps the most the ledger ever held (selfcheck_posted). A cell that falls by more than $1 is a gap
with its dollars: a newer Production export of the same dates lacks postings the one before had (a filtered or partial
export replaced a complete one), or EMA itself removed them. A payer name that changes group is not a fall: the name
rules change on their own (an alias learned or dropped from the weekly exports' payer ids, the owner's
REMITWATCH_EMA_PAYER_ALIASES, a release's name rules), so the check keeps the group each name had when its marks were
made (meta selfcheck:posted_groups), compares each name's money under that group, and moves the marks to the new groups
once nothing is missing. A cell the move changes keeps the fall it already had, so a leak of less than $1 at a time is
still caught while an alias comes and goes. The check's text then says how many names changed group, and a real loss
read in the same run still shows with its own dollars, under the group the marks were made with. To do: compare the
newest Production export of the dates the offending cells name with the one before; send the complete export again, or
accept the change on the Data tab if EMA itself removed those postings. Accepting moves the marks to what the ledger
holds, so a later fall in the same cell is new and holds the run again. Marks made before 2026-11-28 have no record of
each name's group. The first check after the release sums each month across every payer group: a month whose total did
not fall, and whose fall whole payer names now in another group can explain, starts again under the groups of now, and
the check's text says how many dollars moved that way (if no name changed group, compare the exports as above). A month
that also lost money at that check keeps its marks, so its gap includes the moved name's money there: check the export,
then accept. That check records the groups, so this happens once.

**Items kept after a held run.** A run safe mode held leaves the worklist, and the facts the vanished check compares
against (selfcheck_facts and meta selfcheck:facts_input), as the last good run saved them; only the new items the held
run still lists add their own. So an item an answer settled before the held run leaves on the next good run by its new
facts, and is not kept as "nothing explains why". Items kept before this was fixed (2026-11-28) stay until a person
accepts them on the Data tab.

**An item left the list, or left the record, and the self-check says why** (the checks `vanished` for items people were
shown as work, `left_record` for the others). An item the run no longer makes needs a route out found in the store's own
records, or it is kept on the list ("nothing explains why"). The routes: a person's decision; an item this run made for
the same line, claim or service; its remittance superseded; a payment EMA has since posted in full (`posted_in_ema`:
EMA's checks of the item's 835 trace hold its paid amount within $1 or 0.5%); its claim answered by an 835; its claim
sent again, under a later submission number or under the same claim id after the item was raised (`sent_again`: an 837
of it, or EMA's submission date, on or after the day the item was first seen and after the send the item is about); the
owner's accepted pattern; new facts on its own claim (an item about a payment, such as REMIT_NOT_POSTED or TAKEBACK,
also counts EMA's postings of that payment); a copy of an answer the payer delivered twice; or a rule that stops raising
it on purpose and counts its dollars elsewhere (`retired:<counter>`: `snb_past_limit`, a second payer's share a month
past its last day; `unseen`, a claim no source can show the answer to; `unseen_second_send_nothing_left`;
`open_claim_second_nothing_left` and `open_claim_second_patient_paid`, a claim to the visit's second payer with nothing
left for it, or a share the patient paid; the unseen claims are counted in meta ema:unanswered_unseen and on the Data
tab, the others in meta ema:wiring). Detection hands the check the ids it retired, and the decision log names the same
counter as the rule of the item's "gone" row (`detect:snb_past_limit`, where it used to say `detect:no_longer_raised`).
The retired route is only for an item nobody worked: one a person worked stays on the list. The fingerprints the facts
route compares are made one way per release (meta selfcheck:facts_version, 4 since 2026-11-28): the first run after a
release that changes them compares none, so an item that leaves in that run on new facts alone is kept once and waits
for a person on the Data tab. To do: the bundle's examples for the check; an item with no route is a rule change or a
bug.

**An item to work was already paid** (the self-check `precision_guard`, critical: it holds the run past 5 new items). It
looks for money paid on the item's bill, date and code after the item's own answer, from the 835s (net of reversals) and
from EMA's postings on checks no 835 carries, covering what the item says is missing. For a downcoding or an underpayment
only its own payer group's money counts (a second payer paying the copay is not the first payer's level or rate). A
payment counts net of what the payer later took back by a provider-level adjustment (PLB WO, FB, J1 or L6 naming the
paying claim by its claim id or the payer's claim number, dated on or after the payment), and a later answer that
repeats the item's own (the same claim id, code and paid amount, often the plan's network answering under another
name) is no new money. To do: hand-check the examples; a real offender is an item a rule should have closed.

**A claim the link map leaves out** (`ema_link_map.conflict` = 1: EMA's claim is joined to no bill, and its items get no
EMA facts). A link takes the bill that holds at least 2/3 of its payment votes. A split under that is no conflict when it
is one visit's: every vote chose its bill by the patient's name and every vote is for one date of service (the visit
billed to a payer twice, say first as primary in error and then as secondary). The link keeps the bill of its first
vote, the one it held before the second bill was answered. A split between two patients, or over two dates, stays a
conflict. The map rule is meta ema:map_rule: a store mapped by another rule maps its conflicts again at the next
refresh, once, with no new report. To do: for a conflict that stays, compare the two bills' 835 claims (patient, date,
codes) with EMA's claim.

**A code bug** (an exception in a step, a category of items gone, a wrong verdict). The exception is logged with its
stack where the step contains it; the self-checks catch the effects they cover (an item with no state, a closure with
no proof, money that does not add up, items that vanished between runs) and hold closures in safe mode. A progress line
the code marks ERROR (a file that failed and was skipped) also makes the run partial, and the panel counts those lines
with the errors. To do: the bundle, a copy, a failing test, the fix, the four environments.

**Items changed and nobody knows why.** Every automatic change to an item is in item_decisions with its rule, the rule
version (the rules hash of detect.py and the files it reads), the evidence keys, and before and after: raised, closed
with proof, reopened, hidden by a rule, signal changed, folded into another, no longer raised, state, outcome,
deadline, dollars or follow-up day moved. A change a person made stays in their own history (finding_events).

**A feed is stale.** STALE_FEED (no new 835 file for more than ten days) and EMA_STALE (a report more than eight days old)
alerts, and the pages say in dollars what cannot be checked. To do: the weekly EMA routine (docs/OPERATIONS.md), the
TriZetto download, the Dropbox folder.

## 4. Reproduce on a copy

Never on the live store, and never with the live feeds, the fax line or mail on. The copy holds patient data: it stays
on the server (or a machine the practice allows for patient data) and is deleted afterwards. Check out the commit the
runs used (bundle.json, `code.commit`; /api/health shows what each site runs).

Server:
1. A copy: `sudo docker compose exec app python -m remitwatch.cli backup --dir /data/scratch` (or the newest file in
   /data/backups), then `sudo gunzip -k /data/scratch/remitwatch-<stamp>.sqlite.gz`.
2. One cycle on the copy with Dropbox, TriZetto, the fax line and mail switched off for that command:
   `sudo docker compose exec -e REMITWATCH_FAX_SENDER=none -e TRIZETTO_SFTP_USER= -e DROPBOX_REFRESH_TOKEN= -e SMTP_HOST= -e ALERT_WEBHOOK_URL= app python -m remitwatch.cli --db /data/scratch/remitwatch-<stamp>.sqlite cycle --skip-sync --no-email --force-detect`
3. Detection alone: `python -m remitwatch.cli --db /data/scratch/remitwatch-<stamp>.sqlite detect`. The copy's logs go
   to /data/scratch/logs, not the live ones.

Serverless:
1. Copy the state folder (Dropbox, /RemitWatch-state) to a local folder.
2. In a shell with none of the site's settings, run the cycle on the copy with no Dropbox client:
   `python -c "from remitwatch.state import LocalState; from remitwatch import cloud; print(cloud.run_cloud_cycle(LocalState('<copy>'), None, email=False, force_detect=True))"`
3. Its logs are in <copy>/logs. Measure memory the way Vercel limits it: the run's manifest has peak_mb per step.

## 5. Where the logs live and for how long

| | server | Vercel |
|---|---|---|
| event log | /data/logs/events-YYYY-MM-DD.jsonl, written as the run goes | in the run's file, written when it ends |
| manifests | /data/logs/runs-YYYY-MM-DD.jsonl | logs/runs/YYYY-MM-DD/<run_id>.jsonl.gz in the state folder |
| packing | gzipped once the day is over | one gzipped file per run |
| kept | 180 days, then 512 MB at most, oldest first | 90 days, then 100 MB at most, oldest first |
| in the store | run_log (200 days), item_decisions (400 days), repair_log (200 days) | the same, in the store image |
| platform log | the container's log: the progress lines | Vercel's function log: one line per run (run id and outcome) |
| run in progress | logs/open/<run_id>.json, rewritten at each step | the same in the state folder, at each step and every 20 s within one |

Dropping old logs is itself logged (event logs_pruned). Logs are never in git (data/ is ignored).

## 6. Tests

Four environments, all must pass before a release (CLAUDE.md says why each exists): the newest Python with a recent
SQLite; the same with the newest libraries; Python 3.12 with SQLite 3.45 (AWS and CI); Python 3.11 with SQLite 3.40.0
(Vercel; `uv python install 3.11.1`). Plus `node tests/calc_model_check.js` and `node tests/lab_review_check.js` when
pages changed. The logs, repairs and bundle have their own tests: tests/test_runlog.py, tests/test_repair.py,
tests/test_diagnose.py.

## 7. Release the fix

A branch, the full suite in the four environments on a clean checkout, then push to main (both sites deploy). Check
/api/health on both sites for the new commit, then the next run's outcome in the Data tab and its log.
