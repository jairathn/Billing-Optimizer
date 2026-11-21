# RemitWatch on a server: runbook

One host runs everything with Docker Compose: the dashboard (`app`, uvicorn on port 8000, not exposed) and Caddy (HTTPS on
80/443, certificate from Let's Encrypt for `SITE_ADDRESS`). The store is `/data/remitwatch.sqlite` on the `remitwatch-data`
volume. No outside cron is needed: the app's own scheduler runs the cycles (see "What runs by itself").

Paths below assume the repository is at `/opt/remitwatch`; run the commands from there (`cd /opt/remitwatch`).

## Production .env (names only)

Copy each value from Vercel (project, Settings, Environment Variables). Keep the file root-only: `chmod 600 .env`.

Required:

- `SITE_ADDRESS`: the host name the site answers on (it must point at this server: Caddy gets the certificate for it)
- `REMITWATCH_USER`, `REMITWATCH_PASSWORD`
- `DROPBOX_APP_KEY`, `DROPBOX_APP_SECRET`, `DROPBOX_REFRESH_TOKEN`
- `REMITWATCH_SYNC_INTERVAL_MINUTES`: `60` (unset, the scheduler still runs one cycle a day)

The same as on Vercel, wherever Vercel has them:

- `DROPBOX_ROOT_PATH`, `DROPBOX_PATH_ROOT_NS`
- `REMITWATCH_OWNER_PASSWORD`, `REMITWATCH_SHOW_PHI`
- `ANTHROPIC_API_KEY`
- `PRACTICE_NAME`, `PRACTICE_ADDRESS`, `PRACTICE_TIN`, `PRACTICE_NPI`, `PRACTICE_PHONE`, `PRACTICE_FAX`, `PRACTICE_EMAIL`
- `MODMED_CLIENT_ID`, `MODMED_PRIVATE_KEY` (and only if Vercel has them: `MODMED_CLIENT_SECRET`, `MODMED_API_KEY`,
  `MODMED_FHIR_BASE`, `MODMED_TOKEN_URL`, `MODMED_SCOPE`, `MODMED_AUDIENCE`, `MODMED_KEY_ID`, `MODMED_GROUP_ID`)
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM`, `SMTP_STARTTLS`, `REPORT_TO`, `ALERT_WEBHOOK_URL`,
  `APPEAL_EMAIL_DOMAINS`, `APPEAL_EMAIL_TO`
- `SRFAX_ACCESS_ID`, `SRFAX_ACCESS_PWD`, `SRFAX_CALLER_ID`, `SRFAX_SENDER_EMAIL` (once SRFax is set up)
- `TRIZETTO_SFTP_USER`, `TRIZETTO_SFTP_PASSWORD`, `TRIZETTO_SFTP_HOST`, `TRIZETTO_SFTP_PORT`, `TRIZETTO_SFTP_DIRS`,
  `TRIZETTO_SFTP_HOSTKEY` (only if set up)
- `CRON_SECRET`: optional on a server (only an outside caller of `/api/cron/` needs it)
- Tuning, only if Vercel sets them: `REMITWATCH_REPORT_EVERY_DAYS`, `REMITWATCH_STALE_DAYS`, `REMITWATCH_ALERT_RECENT_DAYS`,
  `REMITWATCH_ALERT_BASELINE_DAYS`, `REMITWATCH_NOTE_MODEL`, `REMITWATCH_NOTE_SCRUB`, `REMITWATCH_NOTE_SEND_SCANS`,
  `REMITWATCH_EMA_CLOSES`, `REMITWATCH_STALE_APPEAL_DAYS`, `REMITWATCH_FRESHEN_HOURS`, `REMITWATCH_WEBHOOK_DEBOUNCE_SECONDS`

Server only (optional): `REMITWATCH_BACKUP_KEEP` (daily copies kept, default 7; 0 turns them off), `REMITWATCH_BACKUP_DIR`
(default `/data/backups`), `REMITWATCH_UPLOAD_DIR` (default `/data/uploads`: leave it unset).

Never on the server: `REMITWATCH_MODE=serverless`, `REMITWATCH_STATE_DIR`, `REMITWATCH_ALLOW_OPEN`, any `VERCEL*`,
`REMITWATCH_MAX_SECONDS`, `REMITWATCH_DEFER_DETECT_FILES`, `REMITWATCH_BACKFILL_ZIP`. `REMITWATCH_DB` and
`REMITWATCH_DATA_DIR` are set by the image (`/data/remitwatch.sqlite`, `/data/dropbox`): leave them out.

## First deploy, with the move from Vercel

Order matters: move the data before the app's first start. Started first, its scheduler would begin a full Dropbox load
into an empty store within a minute.

```
git clone https://github.com/jairathn/Billing-Optimizer /opt/remitwatch && cd /opt/remitwatch
# write .env (above), then:
docker compose build
docker compose run --rm --no-deps app python -m remitwatch.cli import-cloud
docker compose run --rm --no-deps app python -m remitwatch.cli compare-cloud
docker compose up -d
```

`import-cloud` reads the Vercel site's Dropbox state folder (`/RemitWatch-state`, with the same Dropbox settings) and
prints what it moved and the counts to check (remittance files, findings, decisions, payer fax numbers, lessons,
hidden reasons, packets). `compare-cloud` proves it: every table of the state folder's store (with the journal applied)
against the server's, row by row for the work people did (findings, their events, payer fax numbers, faxes, lessons,
hidden reasons, alerts, runs) and by count for the remittance tables; it ends with `"different": 0` and exit code 0.

Then open `https://<SITE_ADDRESS>/setup` (every required line green) and compare the site with Vercel page by page.

### Cutover

1. Tell staff to stop working on the Vercel site.
2. On the server: `docker compose stop app`, then `import-cloud --force` and `compare-cloud` again (the forced move
   replaces whatever the server did while it was being checked with Vercel's latest), then `docker compose up -d`.
3. Dropbox app console, Settings, Webhooks: replace the Vercel address with `https://<SITE_ADDRESS>/api/dropbox/webhook`.
4. ModMed: register `https://<SITE_ADDRESS>/.well-known/jwks.json` as the app's key address (and keep `MODMED_CLIENT_ID`).
5. Turn the Vercel project off (delete it, or remove its environment variables and redeploy), so that two sites never
   pull the same files or send the same fax.

## What carries over, and what does not

Carried, exactly: the whole store (every remittance file read, every remittance, claim, line and adjustment, every
finding with its status, notes, recovery and history, the provider tables, the Dropbox files already read), everything
people did since the Vercel site's last run (its journal: statuses, faxes, payer fax numbers, lessons, hidden reasons),
the appeal packets, the Practice figures, which alerts were already emailed and when the last weekly report went out,
the saved weekly reports, and where the site had got to in the Dropbox folder (so no file is downloaded again).

Not carried, by design: the dashboard's serving copy and the last run's summary (`serving.sqlite.gz`, `status.json`;
the server reads its store directly and keeps its own run record), the lock, browser-only things (the owner's password
typed into the Providers tab, remembered filters), and the settings (the .env above).

## Everyday commands

```
docker compose ps                                           # both services running?
docker compose logs --tail 200 app                          # the app's log (add -f to follow)
docker compose exec app python -m remitwatch.cli cycle      # one cycle by hand: Dropbox, ingest, analysis, alerts, faxes, backup
docker compose exec app python -m remitwatch.cli stats      # counts in the store
docker compose exec app python -m remitwatch.cli backup     # a copy of the store now
git pull && docker compose up -d --build                    # update to the newest version on main
docker compose restart caddy                                # after an update that changed deploy/Caddyfile
docker compose restart app                                  # after changing .env
```

A cycle started by hand waits for (or skips) one already running: they never overlap.

## What runs by itself

- A cycle every `REMITWATCH_SYNC_INTERVAL_MINUTES` (at least once a day even when unset), 45 seconds after the app
  starts, whenever Dropbox calls the webhook, when someone presses Sync now or uploads a file, and when someone opens the
  dashboard after 3 hours without one. A cycle is: new Dropbox files (and TriZetto's, if set up, and what was uploaded by
  hand), ingest, analysis when anything is new,
  alerts and their emails, the weekly report when due (saved in `/data/reports`), then the fax line (the state of faxes
  sent, busy lines tried again, payers' faxes read, follow-ups that are due) and the day's backup. This is what Vercel's
  two daily calls (`/api/cron/cycle` at 10:17 UTC and `/api/cron/faxes` at 15:43 UTC) do there. Both addresses also work
  on the server, with `CRON_SECRET`, for an outside scheduler if one is ever wanted.

## Uploads by hand (until ModMed's API is approved)

`https://<SITE_ADDRESS>/upload` (the dashboard's login) takes EMA's exports, remittance and claim files and visit notes;
`docs/OPERATIONS.md` ("EMA by hand") has the person's steps. Nothing to configure. On the server:

- Files are kept on the data volume beside the store, owner-only: `/data/uploads/exports/` (EMA's exports),
  `/data/uploads/remittances/` (835 and 837 files), `/data/uploads/notes/` (visit notes). A file being received is written
  to `/data/uploads/.incoming/` and moved or removed when it is done. Never in the repository, never emailed.
- An export or remittance file waits (`uploads` table, status `queued`) for the next cycle, which reads it with the
  Dropbox loader and notes what it added; the upload wakes the scheduler, which waits its usual 45 seconds for the rest of
  a batch. To read waiting files at once: `docker compose exec app python -m remitwatch.cli cycle`.
- Caddy accepts up to 260 MB on `/api/uploads` (the app's own limit is 250 MB per file) and 20 MB everywhere else.
- Backups: the store's daily copy holds what was read from the files and the record of every upload; the files themselves
  are on the same volume and are covered by its daily snapshot (below).

## Backups

- Every day the first cycle writes a consistent, gzipped copy of the store (SQLite's online backup, safe while the site
  runs) to `/data/backups/remitwatch-<date>-<time>.sqlite.gz`, owner-only, and keeps the newest 7. These undo a mistake or
  a damaged file.
- They sit on the same disk as the store, so the disk itself must be backed up too: snapshot the server's EBS volume
  daily (AWS Data Lifecycle Manager, keep 14; `deploy/aws/remitwatch.yaml` sets this up when the server is made from
  it). The volume's files are at `docker volume inspect remitwatch_remitwatch-data` (Mountpoint) on the host.
- To restore a copy:

  ```
  docker compose stop app
  docker compose run --rm --no-deps app sh -c 'gunzip -c /data/backups/<file>.sqlite.gz > /data/remitwatch.sqlite'
  docker compose up -d
  ```
