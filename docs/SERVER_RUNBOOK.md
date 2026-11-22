# RemitWatch on a server: runbook

One host runs everything with Docker Compose: the dashboard (`app`, uvicorn on port 8000, not exposed) and Caddy (HTTPS on
80/443, certificate from Let's Encrypt for `SITE_ADDRESS`). The store is `/data/remitwatch.sqlite` on the `remitwatch-data`
volume. No outside cron is needed: the app's own scheduler runs the cycles (see "What runs by itself").

Paths below assume the repository is at `/opt/remitwatch`; run the commands from there (`cd /opt/remitwatch`). They start
with `sudo`: the Session Manager user is not root, and Docker and `.env` are root's. New versions on main deploy
themselves ("Automatic deploys" below).

## Production .env (names only)

Copy each value from Vercel (project, Settings, Environment Variables). `deploy/aws/configure.sh` asks for most of them
by name and lists the ones it does not ask for. Keep the file root-only: `sudo chmod 600 .env`.

Required:

- `SITE_ADDRESS`: the host name the site answers on (it must point at this server: Caddy gets the certificate for it)
- `REMITWATCH_USER`, `REMITWATCH_PASSWORD`. Without a password the site serves no data at all: every page goes to
  `/setup`, which says to run configure.sh, and the log says the same at start. A new password must be 12 or more plain
  letters and digits. A shorter one already in use keeps working, so an update never locks the staff out, but `/setup`
  and the log warn about it, and configure.sh offers a new one. (Vercel stays locked with a short one.)
- `DROPBOX_APP_KEY`, `DROPBOX_APP_SECRET`, `DROPBOX_REFRESH_TOKEN`
- `REMITWATCH_SYNC_INTERVAL_MINUTES`: `60` (unset, the scheduler still runs one cycle a day)

The same as on Vercel, wherever Vercel has them:

- `DROPBOX_ROOT_PATH`, `DROPBOX_PATH_ROOT_NS`, `REMITWATCH_STATE_PATH` (the Vercel site's state folder, which
  `import-cloud` and `compare-cloud` read; default `/RemitWatch-state`)
- `REMITWATCH_OWNER_PASSWORD`, `REMITWATCH_SHOW_PHI`
- `ANTHROPIC_API_KEY`
- `PRACTICE_NAME`, `PRACTICE_ADDRESS`, `PRACTICE_TIN`, `PRACTICE_NPI`, `PRACTICE_PHONE`, `PRACTICE_FAX`, `PRACTICE_EMAIL`
- `MODMED_CLIENT_ID`, `MODMED_PRIVATE_KEY` (and only if Vercel has them: `MODMED_CLIENT_SECRET`, `MODMED_API_KEY`,
  `MODMED_FHIR_BASE`, `MODMED_TOKEN_URL`, `MODMED_SCOPE`, `MODMED_AUDIENCE`, `MODMED_KEY_ID`, `MODMED_GROUP_ID`)
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM`, `SMTP_STARTTLS`, `REPORT_TO`, `ALERT_WEBHOOK_URL`,
  `APPEAL_EMAIL_DOMAINS`, `APPEAL_EMAIL_TO`
- `SRFAX_ACCESS_ID`, `SRFAX_ACCESS_PWD`, `SRFAX_CALLER_ID`, `SRFAX_SENDER_EMAIL` (once SRFax is set up; `SRFAX_URL` only
  if Vercel has it). Only one site sends faxes: `REMITWATCH_FAX_SENDER` names it. Unset and `server` mean the same:
  this server sends. `none` pauses faxing. Leave it unset here (except during the first deploy, below), and never set
  it to `serverless` on Vercel while this server runs. `/setup` says which site sends.
- `TRIZETTO_SFTP_USER`, `TRIZETTO_SFTP_PASSWORD`, `TRIZETTO_SFTP_HOST`, `TRIZETTO_SFTP_PORT`, `TRIZETTO_SFTP_DIRS`,
  `TRIZETTO_SFTP_HOSTKEY` (only if set up); `TRIZETTO_SFTP_PASSWORD_SET` (written by configure.sh when a new password is
  entered) and `TRIZETTO_SFTP_PASSWORD_DAYS` (how long TriZetto lets it last, in days), for the reminder before it expires.
  `TRIZETTO_SFTP_SERVERLESS` stays unset here (the server always reads TriZetto), and on Vercel too unless Vercel is the
  only site reading TriZetto.
- `CRON_SECRET`: configure.sh makes one. The server does not need it: its own cycles do what `/api/cron/` does on Vercel
  ("What runs by itself")
- Tuning, only if Vercel sets them: `REMITWATCH_REPORT_EVERY_DAYS`, `REMITWATCH_STALE_DAYS`, `REMITWATCH_ALERT_RECENT_DAYS`,
  `REMITWATCH_ALERT_BASELINE_DAYS`, `REMITWATCH_NOTE_MODEL`, `REMITWATCH_NOTE_SCRUB`, `REMITWATCH_NOTE_SEND_SCANS`,
  `REMITWATCH_EMA_CLOSES`, `REMITWATCH_STALE_APPEAL_DAYS`, `REMITWATCH_FRESHEN_HOURS`, `REMITWATCH_WEBHOOK_DEBOUNCE_SECONDS`,
  `REMITWATCH_NO_ANSWER_DAYS`
- `REMITWATCH_BULK_APPEAL`: leave it unset. Faxing many appeals at once stays off until RemitWatch reads EMA's payments;
  one appeal a person opens and checks still goes.

Server only (optional): `REMITWATCH_BACKUP_KEEP` (daily copies kept, default 7; 0 turns them off), `REMITWATCH_BACKUP_DIR`
(default `/data/backups`), `REMITWATCH_UPLOAD_DIR` (default `/data/uploads`: leave it unset).

Never on the server: `REMITWATCH_MODE=serverless`, `REMITWATCH_STATE_DIR`, `REMITWATCH_ALLOW_OPEN` (it opens the site
with no password: for a developer's own machine only), any `VERCEL*`, `REMITWATCH_MAX_SECONDS`,
`REMITWATCH_DEFER_DETECT_FILES`, `REMITWATCH_BACKFILL_ZIP`, `REMITWATCH_TMP` (a scratch folder for tests). `REMITWATCH_DB`
and `REMITWATCH_DATA_DIR` are set by the image (`/data/remitwatch.sqlite`, `/data/dropbox`), `REMITWATCH_COMMIT` by the
build (the commit `/api/health` shows) and `REMITWATCH_TRIGGER` by the scheduler for its own runs: leave them out.

A change to `.env` takes effect when the app is made again from it: `sudo docker compose up -d` (configure.sh does this
itself). `docker compose restart` does not do it: a restarted container keeps the settings it was made with.

## First deploy, with the move from Vercel

Order matters: move the data before the app's first start. Started first, its scheduler would begin a full Dropbox load
into an empty store within a minute.

```
sudo git clone https://github.com/jairathn/Billing-Optimizer /opt/remitwatch && cd /opt/remitwatch
# write .env (above), with REMITWATCH_FAX_SENDER=none until the cutover, then:
sudo sh -c 'cd /opt/remitwatch && REMITWATCH_COMMIT=$(git rev-parse HEAD) docker compose build'
sudo docker compose run --rm --no-deps app python -m remitwatch.cli import-cloud
sudo docker compose run --rm --no-deps app python -m remitwatch.cli compare-cloud
sudo docker compose up -d
```

The build line gives the image its commit, so `/api/health` can say what runs (a plain `sudo docker compose build`
works too; `/api/health` then shows `"commit": null`). `import-cloud` reads the Vercel site's Dropbox state folder
(`/RemitWatch-state`, with the same Dropbox settings) and prints what it moved and the counts to check (remittance files,
findings, decisions, payer fax numbers, lessons, hidden reasons, packets). `compare-cloud` proves it: every table of the
state folder's store (with the journal applied) against the server's, row by row for the work people did (findings,
their events, payer fax numbers, faxes, lessons, hidden reasons, alerts, runs) and by count for the remittance tables; it
ends with `"different": 0` and exit code 0.

Something people did on the Vercel site that still waits for an item its store does not hold yet stops the move:
`import-cloud` lists each one (when, what, the item ids, who; never the note) and moves nothing. Press "Sync and analyse
now" on the Vercel site, let the run finish, and run the move again. To move without them, add `--drop-waiting`: they
are listed in the answer as `journal_left_behind`, to be done again by hand on the server. `compare-cloud` counts such
entries as a difference ("journal entries still waiting").

Then open `https://<SITE_ADDRESS>/setup` (every required line green) and compare the site with Vercel page by page.
Until the cutover no site faxes: the server has `REMITWATCH_FAX_SENDER=none`, and the Vercel site sends nothing unless
it has `REMITWATCH_FAX_SENDER=serverless`. That keeps the record of every fax in one store: the cutover's forced move
replaces the server's store, and a fax the server sent before it would drop out of the record (a follow-up could then go
twice). Follow-ups that fall due in those days go after the cutover.

### Cutover

1. Tell staff to stop working on the Vercel site.
2. On the server, the move again with `--force`, which replaces what the server did while it was being checked with the
   Vercel site's latest:

   ```
   sudo docker compose stop app
   sudo docker compose run --rm --no-deps app python -m remitwatch.cli import-cloud --force
   sudo docker compose run --rm --no-deps app python -m remitwatch.cli compare-cloud
   ```

   This is the only time `--force` is used. Once staff work on the server, its store is the only one with their work:
   never move the Vercel site's data onto it again. `configure.sh` only offers the move while the server's store is
   empty.
3. Remove the line `REMITWATCH_FAX_SENDER=none` from `/opt/remitwatch/.env`, then `sudo docker compose up -d`: the server
   sends faxes from now on.
4. Dropbox app console, Settings, Webhooks: replace the Vercel address with `https://<SITE_ADDRESS>/api/dropbox/webhook`.
5. ModMed: register `https://<SITE_ADDRESS>/.well-known/jwks.json` as the app's key address (and keep `MODMED_CLIENT_ID`).
6. Turn the Vercel project off (delete it, or remove its environment variables and redeploy), so that two sites never
   pull the same files or send the same fax.

## What carries over, and what does not

Carried, exactly: the whole store (every remittance file read, every remittance, claim, line and adjustment, every
finding with its status, notes, recovery and history, the provider tables, the Dropbox files already read), everything
people did since the Vercel site's last run (its journal: statuses, faxes, payer fax numbers, lessons, hidden reasons),
the appeal packets, the Practice figures, which alerts were already emailed and when the last weekly report went out,
the saved weekly reports, and where the site had got to in the Dropbox folder (so no file is downloaded again). A
journal entry that still waits for its item stops the move until the Vercel site's next run adds the item, or is left
behind and listed with `--drop-waiting` (above).

Not carried, by design: the dashboard's serving copy and the last run's summary (`serving.sqlite.gz`, `status.json`;
the server reads its store directly and keeps its own run record), the lock, browser-only things (the owner's password
typed into the Providers tab, remembered filters), and the settings (the .env above).

## Everyday commands

```
sudo docker compose ps                                           # both services running?
sudo docker compose logs --tail 200 app                          # the app's log (add -f to follow)
sudo docker compose exec app python -m remitwatch.cli cycle      # one cycle by hand: Dropbox, ingest, analysis, alerts, faxes, backup
sudo docker compose exec app python -m remitwatch.cli stats      # counts in the store
sudo docker compose exec app python -m remitwatch.cli backup     # a copy of the store now
sudo docker compose up -d                                        # after changing .env: the app is made again with the new values
sudo docker compose stop app                                     # stop the app (it lets a request in progress finish: up to 2 minutes)
sudo bash /usr/local/lib/remitwatch/auto-deploy.sh --status      # what runs, main's newest commit, the last deploys
sudo systemctl start remitwatch-deploy                           # look for a new commit now instead of in up to 5 minutes
sudo bash /usr/local/lib/remitwatch/auto-deploy.sh --force       # deploy main's newest commit now, even one that was held
curl -s https://<SITE_ADDRESS>/api/health                        # no login: the commit the running site was built from
```

Run things by hand in the app's own container (`docker compose exec app ...`, as above), not in a container of their
own (`docker compose run`): the deploy and the app's next start can then see the run and wait for it.

Runs never overlap. A cycle started by hand while another is running is skipped: it prints "another cycle is running"
and does nothing. The cycle's fax step, `fax-cycle` and the Faxes page's two buttons share a lock of their own: a second
fax run skips while one is going (`fax-cycle` then exits 1). Each fax is also written down before it is sent, so two
presses of Appeal by fax for one item send it once. One copy of the store is made at a time (`backup` exits 1 while
another is being made).

A stop ends the cycle running in the app and frees its locks: a restart, `up -d` after a change to `.env`, a reboot. After
a stop with no shutdown (a power cut, AWS's own recovery, a kill), the next start frees the locks the runs left. The next
cycle is never held up for hours.

## What runs by itself

- A cycle every `REMITWATCH_SYNC_INTERVAL_MINUTES` (at least once a day even when unset), 45 seconds after the app
  starts, whenever Dropbox calls the webhook, when someone presses Sync now or uploads a file, and when someone opens the
  dashboard after 3 hours without one. A cycle is: new Dropbox files (and TriZetto's, if set up, and what was uploaded by
  hand), ingest, analysis when anything is new,
  alerts and their emails, the weekly report when due (saved in `/data/reports`), then the fax line (the state of faxes
  sent, busy lines tried again, payers' faxes read, follow-ups that are due) and the day's backup. This is what Vercel's
  two daily calls (`/api/cron/cycle` at 10:17 UTC and `/api/cron/faxes` at 15:43 UTC) do there. No cron or outside
  scheduler is needed here, and none should call those addresses: the cycles already do that work.
- New versions of RemitWatch from main ("Automatic deploys", next).
- System updates, every night ("System updates", below).

## Automatic deploys

The server follows the repository's main branch by itself, as Vercel did: a merge to main is live within about 5
minutes, with nobody on the box. Switch it on once (a server built from the template, and `configure.sh` when you answer
yes, have it on already):

```
sudo bash -c 'cd /opt/remitwatch && mkdir -p /var/lib/remitwatch-deploy && git rev-parse HEAD > /var/lib/remitwatch-deploy/deployed && git pull --ff-only && bash deploy/install-auto-deploy.sh'
```

That notes the commit running now (so a first deploy that fails goes back to it), installs `remitwatch-deploy.timer`,
deploys main's newest commit at once and leaves the timer running. `sudo bash /opt/remitwatch/deploy/install-auto-deploy.sh
--off` switches it off; the log and state stay.

### Who controls the server

Whoever can push to main controls this server. Each deploy builds the image from the repository's `Dockerfile` and
`requirements.txt` and starts it with the repository's `docker-compose.yml`, as root, within about 5 minutes, with nobody
checking. A commit can change what those files do, and so can a leaked GitHub token or login. So protect main in GitHub:
the repository, Settings, Branches (or Rules), a rule for `main` that requires a pull request with at least one
approving review, and blocks force pushes and deletion. Keep the people who can push to main, or approve, to the few who
look after the site. The rule covers every change, including the ones a Claude session makes: they then arrive as pull
requests that someone approves. Whether to switch it on is the owner's decision.

The deploy script itself is not taken from main on each run. The timer runs a copy that the installer put in
`/usr/local/lib/remitwatch/auto-deploy.sh` (root's only). A commit that changes `deploy/auto-deploy.sh`,
`deploy/install-auto-deploy.sh` or the timer's files under `deploy/systemd/` is deployed like any other, but the new
versions of those files do nothing until a person reads them and runs:

```
sudo bash /opt/remitwatch/deploy/install-auto-deploy.sh --update
```

The deploy log says when a deploy brought such a change, and `--status` says when the checkout's script differs from the
copy that runs. Running configure.sh again says so too, and installs nothing. The installer refuses a script that does
not parse, and the copy that runs stays as it was.

### How it works

`/usr/local/lib/remitwatch/auto-deploy.sh`, run every 5 minutes as root by the timer:

- `git fetch`. When origin/main is the commit already running (`/var/lib/remitwatch-deploy/deployed`), nothing happens
  and nothing is logged.
- While the app is stopped on purpose (`sudo docker compose stop app`), nothing is deployed: a deploy would start it.
  `--force` deploys anyway.
- Only docs changed (files under `docs/` and `*.md`): the files are checked out; nothing is rebuilt or restarted.
- Anything else: build first, swap second. The files are checked out, Caddy's image is refreshed, `docker compose build
  --pull` runs (fresh base images, so security fixes in them arrive), and Caddy checks its file with the image about to
  run (`caddy validate`). A new base image also installs every Python package again, at its newest version
  (`requirements.txt` sets lower bounds only), and only the site answering checks them. Pinning the versions is the
  owner's decision. A failed build, or a Caddy file Caddy cannot load, puts the files and images back, leaves the
  running site as it was, and is logged with its output. The next tick tries again, and after three failures of the same
  commit it is tried once an hour (a new commit on main is tried at once).
- No swap while a data cycle, a fax run or a backup is going in the app (each holds a lock in the store). A deploy waits
  up to 5 minutes for it to end. If it is still going, everything is put back and a later check deploys after it ends.
- Then `docker compose up -d`, and Caddy is restarted when `deploy/Caddyfile` changed.
- The site must answer within 3 minutes, in the app and through Caddy (HTTPS on this server's port 443, as visitors
  reach it). If it does not (or `up -d` fails), the images and files that ran before are put back and started again, and
  the new commit is held: it is not tried again by itself (push a fix, or `--force` it). A commit is recorded as
  deployed only once the site answers. The check through Caddy uses `SITE_ADDRESS`: until that address reaches this
  server, Caddy has no certificate for it and every deploy is rolled back, so point the address first.
- Files changed by hand in `/opt/remitwatch` (any tracked file that `git status` lists) stop it, and so does a commit that
  would overwrite a file git does not track there (a local override, or `.env` itself): nothing is overwritten, and the
  log says so once. Put the files back (`sudo git checkout -- .`) or make the change on main; move an untracked file out
  of the way. `.env` is never touched.
- A failure also goes to the chat webhook in `ALERT_WEBHOOK_URL`, when `.env` has one: the first failure of a commit,
  a rollback, and files changed by hand. Commits and results only, nothing about patients.
- One deploy at a time: a tick that finds one running (or `configure.sh` running) skips. After each attempt, images that
  nothing uses any more are pruned, with build cache no build has used for 30 days and the build logs beyond the last
  30, so the disk does not fill.

Reading its log: one line per deploy, tab-separated: time (UTC), the commit it came from, the commit it went to, the
result, and the detail.

```
sudo tail -n 20 /var/log/remitwatch-deploy.log
2026-11-21T22:05:31Z  from=8f1ca8875eeb  to=d55184cacaf6  deployed  212s; 21 file(s) changed. Output: /var/lib/remitwatch-deploy/builds/…
```

Results: `deployed`; `docs-only` (checked out, no rebuild); `build-failed` and `caddy-refused` (the site untouched; the
build's own output is in `/var/lib/remitwatch-deploy/builds/`, the last 30 kept); `waiting` (a run was going: tried
again later); `unhealthy` or `up-failed` (rolled back and held); `refused` (files changed by hand);
`checkout-failed`. The timer's own runs: `journalctl -u remitwatch-deploy`; `systemctl list-timers remitwatch-deploy.timer`
shows the next one.

### Which commit runs

`https://<SITE_ADDRESS>/api/health` needs no login and shows `commit`: on this server, the commit the running image was
built from; on the Vercel site, the commit Vercel built. So the two sites can be compared without a shell on the server.

- After a rollback it is the commit that runs again.
- After a commit that changes only docs nothing is rebuilt, so the server keeps showing the commit before it, while Vercel
  (which builds every commit) shows the newer one. `git diff --stat <server's> <Vercel's>` then lists only docs.
- It is `null` for an image built without it: a plain `sudo docker compose build`, a server first booted from an older
  template, and the first deploy made by an older deploy script. The next deploy, or configure.sh, names it again.
- The same commit means the same code, not the same library versions: `requirements.txt` sets lower bounds only, and each
  site installs what is newest when it builds.

## System updates

Every night, between 08:40 and 09:00 UTC, the server installs the latest Amazon Linux release
(`dnf -y --releasever=latest upgrade`, as AWS documents: Amazon Linux 2023 otherwise stays on the release its image was
made from and receives nothing). When an update needs a reboot (a new kernel), the server reboots in the same quiet
hour, after a deploy and a data cycle or fax run have ended (it waits up to an hour for them). The site comes back by
itself. The last runs: `journalctl -u remitwatch-os-update`.

A server made from the template since this was added has it. On a server made before, set it up once (it also turns
off `dnf-automatic`, which installed nothing there):

```
sudo dnf -y install dnf-plugins-core
sudo tee /etc/systemd/system/remitwatch-os-update.service >/dev/null <<'UNIT'
[Unit]
Description=Amazon Linux updates (the latest release), and a reboot when one needs it
Wants=network-online.target
After=network-online.target
[Service]
Type=oneshot
ExecStart=/usr/bin/dnf -y --releasever=latest upgrade
ExecStartPost=/bin/sh -c 'dnf needs-restarting -r >/dev/null 2>&1 && exit 0; mkdir -p /var/lib/remitwatch-deploy; exec flock -w 3600 /var/lib/remitwatch-deploy/lock /bin/sh -c "test -f /usr/local/lib/remitwatch/auto-deploy.sh && /bin/bash /usr/local/lib/remitwatch/auto-deploy.sh --idle 3600; systemctl reboot"'
UNIT
sudo tee /etc/systemd/system/remitwatch-os-update.timer >/dev/null <<'UNIT'
[Unit]
Description=Amazon Linux updates every night
[Timer]
OnCalendar=*-*-* 08:40:00 UTC
RandomizedDelaySec=20min
[Install]
WantedBy=timers.target
UNIT
sudo systemctl daemon-reload
sudo systemctl disable --now dnf-automatic.timer 2>/dev/null; sudo systemctl enable --now remitwatch-os-update.timer
```

To update at once instead of waiting for the night: `sudo systemctl start remitwatch-os-update`.

## Uploads by hand (until ModMed's API is approved)

`https://<SITE_ADDRESS>/upload` (the dashboard's login) takes EMA's exports, remittance and claim files and visit notes;
`docs/OPERATIONS.md` ("EMA by hand") has the person's steps. Nothing to configure. On the server:

- Files are kept on the data volume beside the store, owner-only: `/data/uploads/exports/` (EMA's exports),
  `/data/uploads/remittances/` (835 and 837 files), `/data/uploads/notes/` (visit notes). A file being received is written
  to `/data/uploads/.incoming/` and moved or removed when it is done. Never in the repository, never emailed.
- An export or remittance file waits (`uploads` table, status `queued`) for the next cycle, which reads it with the
  Dropbox loader and notes what it added; the upload wakes the scheduler, which waits its usual 45 seconds for the rest of
  a batch. To read waiting files at once: `sudo docker compose exec app python -m remitwatch.cli cycle`.
- Caddy accepts up to 260 MiB (272,629,760 bytes) on `/api/uploads`, more than the app's own limits (240 MiB per upload,
  60 MiB per EMA export, 15 MiB per visit note), so a file too large gets the app's own plain answer. Everywhere else
  Caddy takes up to 20 MB.
- A spreadsheet that is none of the three exports RemitWatch reads (an A/R or production report, say) is refused with the
  reason, and not kept.
- Backups: the store's daily copy holds what was read from the files and the record of every upload; the files themselves
  are on the same volume and are covered by its daily snapshot (below).

## Backups

- Every day the first cycle writes a consistent, gzipped copy of the store (SQLite's online backup, safe while the site
  runs) to `/data/backups/remitwatch-<date>-<time>.sqlite.gz`, owner-only, and keeps the newest 7. These undo a mistake or
  a damaged file. Each copy is written under a `.part` name and renamed only when it is whole; what a copy cut short (a
  deploy, a reboot) left behind is removed by the next one. One copy is made at a time, under a lock the deploy waits
  for.
- They sit on the same disk as the store, so the disk itself must be backed up too: snapshot the server's EBS volume
  daily (AWS Data Lifecycle Manager, keep 14; `deploy/aws/remitwatch.yaml` sets this up when the server is made from
  it). The volume's files are at `docker volume inspect remitwatch_remitwatch-data` (Mountpoint) on the host.
- To restore a copy (it replaces everything done since that copy was made). First list the copies and choose one; their
  names sort oldest first:

  ```
  cd /opt/remitwatch
  sudo docker compose exec app ls -l /data/backups
  sudo mkdir -p /var/lib/remitwatch-deploy && sudo flock /var/lib/remitwatch-deploy/lock bash
  ```

  The last line opens a root shell that holds the deploy lock, so no deploy starts the app halfway through (it first
  waits for a deploy that is running to end). In that shell, with the copy's name as listed (it ends in `.sqlite.gz`)
  in place of `<file>`:

  ```
  docker compose stop app
  docker compose run --rm --no-deps app sh -c 'cd /data && gunzip -c backups/<file> > restoring.sqlite && rm -f remitwatch.sqlite-wal remitwatch.sqlite-shm && mv restoring.sqlite remitwatch.sqlite'
  docker compose run --rm --no-deps app python -c "import sqlite3; c = sqlite3.connect('/data/remitwatch.sqlite'); print(c.execute('PRAGMA integrity_check').fetchone()[0]); c.execute(\"DELETE FROM meta WHERE key LIKE '%:lock'\"); c.commit()"
  docker compose up -d
  exit
  ```

  The `rm -f` of `remitwatch.sqlite-wal` and `remitwatch.sqlite-shm` matters. A run stopped halfway leaves its last
  changes in the `-wal` file, and SQLite would lay them over the restored copy the next time it opens the store: the
  restore would then bring back the very changes it was meant to undo, or mix two states. It comes after the copy is
  unpacked, so a mistyped name changes nothing. The third line prints `ok` when the restored store is whole, and
  removes the run locks the copy may hold, so the next cycle is not held up.
