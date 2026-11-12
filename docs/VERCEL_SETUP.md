# Running RemitWatch on Vercel

Pushing to `main` deploys the site on Vercel. Vercel keeps no files between requests and has no long-running process, so
on Vercel the app runs in its serverless mode: the store lives in a private folder of the practice's own Dropbox and each
update is one time-boxed run. Nothing else has to be hosted.

The other way to run it (a small VM with a disk, `docker compose up`) is in [OPERATIONS.md](OPERATIONS.md). The dashboard,
the detection rules and the numbers are the same in both.

## What you need to set

All of these go in Vercel, project, Environment Variables, for Production. Vercel reads them only when it deploys, so
redeploy after every change.

| Name | Where the value comes from | Needed |
| --- | --- | --- |
| `REMITWATCH_PASSWORD` | Made up, 12 or more plain letters and digits. The dashboard password. The site stays locked until it is set and long enough. | Yes |
| `CRON_SECRET` | Made up, 16 characters or more, no spaces. Vercel's scheduler and the site's own follow-up runs present it. | Yes |
| `DROPBOX_APP_KEY` | Dropbox Developer Console, the app's Settings tab | Yes |
| `DROPBOX_APP_SECRET` | Same tab | Yes |
| `DROPBOX_REFRESH_TOKEN` | The Connect Dropbox step on the site's `/setup` page | Yes |
| `DROPBOX_ROOT_PATH` | The data folder. Default `/Trizetto Data`. | No |
| `DROPBOX_PATH_ROOT_NS` | Only for a Dropbox team space. `/setup` shows the number when it is needed. | Sometimes |
| `REMITWATCH_STATE_PATH` | Where the site keeps its own files in Dropbox. Default `/RemitWatch-state`. | No |
| `REMITWATCH_MAX_SECONDS` | Time allowed for one run. Default 240, one minute under the function limit in `vercel.json`. | No |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM`, `REPORT_TO` | Outgoing mail, for alerts and the weekly summary | No |
| `APPEAL_EMAIL_DOMAINS` | The domains appeal packets may be emailed to, comma separated. Until it is set, packets can be downloaded but not emailed. | To email packets |
| `ANTHROPIC_API_KEY` | Turns on the visit-note review for appeal packets. Set it only when a business associate agreement covers that account. | No |

The rest of `.env.example` (alert windows, letterhead, appeal email limits) works the same on Vercel.

## Setup, in order

1. **Dropbox app.** Signed in to dropbox.com as the account that holds the data folder, open
   https://www.dropbox.com/developers/console/apps and click Create app: Scoped access, Full Dropbox, any name. On the
   Permissions tab tick `files.metadata.read`, `files.content.read` and `files.content.write`, then Save changes. On the
   Settings tab copy the App key and the App secret.
2. **First four settings.** In Vercel add `REMITWATCH_PASSWORD`, `CRON_SECRET`, `DROPBOX_APP_KEY` and
   `DROPBOX_APP_SECRET`. Redeploy. Before a password is set, the site's `/setup` page shows one line saying so, and offers
   random values for the first two.
3. **Connect Dropbox.** Open `/setup`, log in (user `oda`), and use the Connect Dropbox box: it links to Dropbox's
   approval page, takes the code Dropbox shows, and returns the long-lived token once. Add it in Vercel as
   `DROPBOX_REFRESH_TOKEN`. Redeploy. The token only carries the permissions ticked when it was made: if you tick one
   later, connect again.
4. **First load.** When every required line on `/setup` has a tick, press Run the first load now. It reads the whole
   folder in several short runs that follow one another. On the five-year archive this was two runs and about two minutes
   of computing.
5. **Instant updates.** `/setup` shows the webhook address. Add it in the Dropbox Developer Console: the app, Settings
   tab, Webhooks, Add webhook. Without it the site still checks Dropbox once a day.

`/setup` checks every setting live and says what to fix. `/api/health` needs no login and shows the version, the commit
and what the runtime offers.

## How it works

- `vercel.json` sends every address to one Python function (`api/index.py`), allows it 300 seconds, and schedules
  `/api/cron/cycle` once a day (10:17 UTC; on the Hobby plan Vercel fires it some time in that hour). `public/` exists
  so that Vercel serves nothing else from the repository.
- Keep `"framework": null` in `vercel.json`. Under Vercel's FastAPI preset the function is given the rewritten path, so
  every request would arrive as `/api/index`.
- Vercel refuses to build when `CRON_SECRET` has a space or a line break at either end.
- One run (`remitwatch/cloud.py`): take a lock file, download the store (`store.sqlite.gz`), apply what people recorded
  since the last run, list what is new in the data folder, ingest it, detect, raise alerts, save the store, save a small
  copy for the dashboard built from it (`serving.sqlite.gz`), send mail, write the status, release the lock.
- After detection the same run rebuilds the provider tables (`remitwatch/activity.py`): about seven seconds on the full archive and
  no more memory, because it runs once detection has given its memory back. They are also rebuilt once, with no new file, after a
  release that changes how they are built.
- The store is saved first because it is the record. If a run dies between the two saves, the next run sees that the
  dashboard's copy is behind, or was made by a different build of the app (`serving.json`), and rebuilds it. Mail goes out after both saves, and what was sent is kept
  in its own small file (`notified.json`), so a failed save or an idle run cannot send the same alert or report twice.
- A run stops ingesting at half its time and saves. If there is more to do it starts a fresh run of itself and returns.
  That relies on Vercel finishing a request's work after the caller hangs up. It does: checked on the live site on
  2026-11-12 (six seconds of work completed after a caller that left at 1.2 seconds).
  Detection is left to a fresh run after a large batch, because a new process starts with clean memory.
- Runs follow one another only while they are getting somewhere, and at most 30 times in a row. A file that cannot be
  read (a damaged spreadsheet, a file over 60 MB, one Dropbox will not release) is noted on the Data tab, remembered and
  passed over until it changes. A download that merely timed out is tried twice more, a minute and a half apart, and
  then left to the next daily run.
- The dashboard never waits for a run. Each instance downloads the dashboard's copy, looks for a newer one every 45
  seconds, and lays the journal of recent status changes over it. A status change is one small file in `actions/`.
- Appeal packets are stored as two files each under `packets/`.
- A Dropbox webhook is verified by its signature, then answered. It starts a run only when no run is going, the last
  one did not fail in the past six hours, die, or leave work unfinished in the past hour, and the data folder has new
  files of a kind the app reads. The site's
  own writes to its state folder never start one. The daily scheduled run is not subject to these conditions.
- A status change recorded for a finding the store does not hold yet is kept in the journal and tried again after
  each detection, for a week.
- `/api/health` runs a self-test once per process: an empty store held in memory, grown, saved, reopened, and the
  dashboard's copy built from it. It shows whether this host can run the mode at all.
- An instance that has just started and cannot reach the state folder answers "could not reach its storage" rather than
  showing an empty dashboard. One that already holds a copy keeps serving it and reports the error on the Data tab.

## Access

- One shared login (user `oda`). Ten wrong passwords from one address lock that address out for ten minutes.
- Add the settings for the Production environment only. A preview build of another branch that was given them would
  hold the Dropbox token; the app refuses to run a cycle on a preview build either way.
- No page can be framed by another site, answers with data are marked not to be stored, and the run address accepts the
  login only from the site's own pages.

## Limits to know about

| Limit | Value | What it means here |
| --- | --- | --- |
| Memory | 2 GB on Vercel's standard function (the only size on the Hobby plan). 4 GB with Function CPU set to Performance (Pro plan). | A detection run on almost three years of remittances peaks at about 1.5 GB (measured on a run that re-analyses a store already holding findings; a first load peaks lower). It grows with the archive. `/setup` and the Data tab show the last run's peak. |
| Time | 300 seconds per run | The run budget is 240 seconds and the work is split across runs, so this is not reached in normal use. |
| Upload size | 4.5 MB per request | A visit note uploaded for an appeal packet can be 3 MB at most on Vercel (15 MB on a VM). |
| Scheduler | Once a day on the Hobby plan | The webhook covers the rest of the day. |
| Store size | 1 GB uncompressed (SQLite's limit for a database held this way) | The store is about 0.5 GB today. |

A run that is killed (out of memory or time) leaves its lock behind. The next run takes it over after seven minutes, and
the setup page and the Data tab say that a run stopped without finishing.

## Agreements

Remittance files, the store and appeal packets contain patient information. On Vercel the store passes through Vercel's
function memory and is kept in Dropbox. Vercel signs a business associate agreement only on its Pro plan (as a paid
add-on) and Enterprise, and its Hobby plan is not offered for commercial use. Dropbox signs one on its team plans. Check
both before real data goes through this deployment, and see [OPERATIONS.md](OPERATIONS.md) for the VM alternative.

## Trying it without Vercel

```bash
REMITWATCH_MODE=serverless REMITWATCH_STATE_DIR=/tmp/rw-state REMITWATCH_PASSWORD=pw CRON_SECRET=0123456789abcdef \
  uvicorn remitwatch.api:app
```

`REMITWATCH_STATE_DIR` keeps the state in a local folder instead of Dropbox. `scripts/cloud_bench.py` runs the cycle one
process per run against a folder standing in for Dropbox and reports the time and peak memory of each.
