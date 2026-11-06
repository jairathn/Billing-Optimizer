# Operations: set up once, then it runs itself

## 1. Dropbox app credentials (15 minutes, once)

1. Go to https://www.dropbox.com/developers/apps and create an app: Scoped access, Full Dropbox (the `Trizetto Data` folder is a shared folder mounted in the owner's Dropbox).
2. Permissions tab: enable `files.metadata.read` and `files.content.read`. Submit.
3. Settings tab: copy the App key and App secret into `.env` (`DROPBOX_APP_KEY`, `DROPBOX_APP_SECRET`).
4. Run `python -m remitwatch.cli oauth`, open the printed link while logged in as the Dropbox account that can see `Trizetto Data`, approve, paste the code. The command prints `DROPBOX_REFRESH_TOKEN=...`; put it in `.env`. Refresh tokens do not expire unless revoked.
5. If the folder is in a Dropbox team space rather than a personal account, set `DROPBOX_PATH_ROOT_NS` to the team root namespace id (the `ns:` number in the connector paths). Leave it empty otherwise.

## 2. First load (one time, on the host)

```bash
python -m remitwatch.cli sync --full          # mirrors the whole folder (about 112 MB of remits), ingests everything new
python -m remitwatch.cli detect
python -m remitwatch.cli stats
```

If `Trizetto.zip` holds history that is no longer in the folder, unzip it into `data/dropbox/Trizetto Data/Trizetto/` before the first `ingest-835`.

## 3. Weekly job

`python -m remitwatch.cli weekly` does: Dropbox delta sync, ingest, detect, report (and email when `SMTP_HOST` and `REPORT_TO` are set). Choose one scheduler:

- cron on a Linux host: `deploy/crontab.example` (Monday 05:10 practice time).
- Docker: `docker compose up -d` runs the dashboard and a Sunday 03:00 UTC job inside the container; `docker compose run --rm app weekly` runs it now.
- A managed container host with a scheduler (Render cron job, Fly machines schedule, Google Cloud Run jobs + Cloud Scheduler, AWS ECS scheduled task): run the image with `weekly` as the command and a persistent volume at `/data`.

The job is idempotent. If a run fails halfway, the next run picks up where it left off.

## 4. Dashboard

`python -m remitwatch.cli serve --host 0.0.0.0 --port 8000` or the container's default command. Put it behind HTTPS (Caddy, nginx, the host's load balancer, or Cloudflare Access) and set `REMITWATCH_PASSWORD`. The detail view shows patient initials only unless `REMITWATCH_SHOW_PHI=1`.

## 5. Where to host (PHI)

The store and the mirror contain PHI. Choose a host that signs a BAA and keep the disk encrypted. Options, cheapest first: a small VM at the practice or in a BAA-covered cloud (AWS Lightsail/EC2, Google Compute Engine, Azure) with Docker; Render or Fly.io on a plan that includes a BAA; a Mac mini or NUC in the office with Tailscale. Vercel's serverless functions have no persistent disk and are not the place for the store; the `api/index.py` entry point remains only for a read-only copy. GitHub Actions should not process PHI (no BAA). See `docs/RESEARCH_TECH.md` for the researched comparison.

## 6. Weekly routine for the billers (about an hour)

1. Open the dashboard, Appeals queue tab: everything appealable or correctable, soonest deadline first.
2. Correctable errors (modifier 25 missing, 57 used on a 0/10-day global, N822): submit corrected claims straight from EMA; mark "resolved".
3. Appeals: open the finding, click "Appeal packet", copy the letter, attach the listed documents, send through the payer portal or fax; mark "appealed".
4. Contract findings (Anthem commercial E/M at 59 to 72 percent of Medicare; Humana MA at 95 percent): these go to the practice administrator, not the billers.
5. Fees: ask Zelis, Optum Pay, Payspan and the TPAs for fee-free standard EFT (ACH) enrollment; the FEE_SKIM findings list which payers and how much.

## 7. Updating

`git pull`, `pip install -r requirements.txt`, then `python -m remitwatch.cli detect` to re-score with new rules. Detection keeps finding statuses.

## 8. Troubleshooting

- `Dropbox files/list_folder failed 409 ... not_found`: the `DROPBOX_ROOT_PATH` is wrong for this account, or the folder is in a team space (set `DROPBOX_PATH_ROOT_NS`).
- `cursor reset`: the job automatically falls back to a full listing.
- Unbalanced transactions in `stats`: a few payers send remits that do not balance; the lines are still loaded and flagged (`balanced = 0`).
- A payer name that lands in "Self-funded plan TPAs" or "Other": add a rule to `PAYER_RULES` in `remitwatch/codes.py` and re-run `detect`.
