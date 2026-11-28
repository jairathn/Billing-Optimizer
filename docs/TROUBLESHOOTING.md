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

**A code bug** (an exception in a step, a category of items gone, a wrong verdict). The exception is logged with its
stack where the step contains it; the self-checks catch the effects they cover (an item with no state, a closure with
no proof, money that does not add up, items that vanished between runs) and hold closures in safe mode. A progress line
the code marks ERROR (a file that failed and was skipped) also makes the run partial, and the panel counts those lines
with the errors. To do: the bundle, a copy, a failing test, the fix, the four environments.

**Items changed and nobody knows why.** Every automatic change to an item is in item_decisions with its rule, the rule
version (the rules hash of detect.py and the files it reads), the evidence keys, and before and after: raised, closed
with proof, reopened, hidden by a rule, signal changed, folded into another, no longer raised, state, outcome,
deadline, dollars or follow-up day moved. A change a person made stays in their own history (finding_events). The run's
own marks are logged even when a person changed the item since the run before: an item the rules no longer list is a
'state' row with the rule detect:no_longer_listed (detect:listed_again when it comes back).

**An item a person worked changed state, or left the list.** A Started counts as a person's only when a person set it on
that item (a status line in its history). A Started the run handed on from a claim's chase (the payer's answer to a
claim someone was chasing starts Started) keeps its label, but the rules judge it as To do: it expires past its window,
waits on EMA's follow-up, is done when paid, and its basis says where the Started came from (states.person_started). A
second payer's bill a person sent (Mark billed sets Sent; their own Started counts too) is never dropped when the second
payer answers. Paid what it owes, to $1, the run closes it as Paid with that payment as its proof (the payer's 835
lines, else EMA's posting), recovered at what arrived, and the decision log says paid_later:paid_as_second_payer
(paid_later:settled_by_the_patient when the patient paid the share). Paid less, or nothing, it stays listed at what is
left with "work its answer". What it owes is the share billed, or less where its own 835 gives its allowed amount (the
owner's decision 3). A person who moves it off that closure keeps their status. A row the run keeps stale is on no
list: the Results tab's open book counts it closed (paid when money came back on it, else no longer listed), never
still open.

**A routine-rate review or a clinician's rate gap expired.** SECOND_RATE and PROVIDER_RATE_GAP items stand for claims
with their own filing windows. When nobody worked one and the last claim's window has closed, it expires (Expired, the
day after), its next step says when that window closed, and the Results scoreboard counts as lost the claims whose
windows closed after it was first flagged, each line once (a one-off item of the same line counts that line, not both).
A person's Started or Sent keeps it listed. The self-check past_window names one still Work now past its last window.

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
