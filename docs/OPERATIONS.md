# Operations: set up once, then it runs itself

## 1. Where to run it

The store, the Dropbox mirror and the appeal packets hold patient information. Run it on a host covered by a business associate agreement (BAA).

Recommended: one small virtual machine in AWS, Google Cloud or Azure. All three sign a BAA at no extra charge (accept it in the account before loading data). Size it at 2 vCPU and 4 GB of memory, because a detection run on the full archive peaks at about 2 GB, with a 20 GB encrypted disk. That is roughly $25 to $35 a month. Install Docker and run `docker compose up -d`; the compose file starts the dashboard, its scheduler and Caddy for HTTPS.

Alternatives that cost more and need less administration: Fly.io with its compliance package, or Render on its HIPAA plan. Check current pricing; in November 2026 they were about $99 and $499 a month.

Vercel also works, and a push to `main` deploys there: the app keeps its store in a private Dropbox folder because Vercel has no disk. Setup and limits are in `VERCEL_SETUP.md`. Two things to weigh: a run needs about 1.4 GB of the 2 GB a standard Vercel function has, and Vercel signs a BAA only on its Pro plan (a paid add-on) or Enterprise.

Not suitable: GitHub Actions for the scheduled job (no BAA), or a workstation that sleeps.

Three other agreements to confirm:

- Dropbox: the remittances already sit there. Dropbox signs a BAA on its team plans only.
- Email: alert and summary emails carry payer names, counts and dollars, never patient data. Appeal packets do carry patient data, so send them through a mail service under a BAA and keep `APPEAL_EMAIL_DOMAINS` set to the practice domain.
- Anthropic: needed only for the visit-note review (section 6).

## 2. Dropbox app credentials (15 minutes, once)

1. Go to https://www.dropbox.com/developers/apps and create an app: Scoped access, Full Dropbox (the `Trizetto Data` folder is a shared folder mounted in the owner's Dropbox).
2. Permissions tab: enable `files.metadata.read` and `files.content.read`. Submit.
3. Settings tab: copy the App key and App secret into `.env` (`DROPBOX_APP_KEY`, `DROPBOX_APP_SECRET`).
4. Run `python -m remitwatch.cli oauth`, open the printed link while logged in as the Dropbox account that can see `Trizetto Data`, approve, paste the code. The command prints `DROPBOX_REFRESH_TOKEN=...`; put it in `.env`. Refresh tokens do not expire unless revoked.
5. If the folder is in a Dropbox team space rather than a personal account, set `DROPBOX_PATH_ROOT_NS` to the team root namespace id. Leave it empty otherwise.

## 3. First load (once)

```bash
docker compose run --rm app python -m remitwatch.cli cycle --full
```

This mirrors the whole folder (about 112 MB of remittances), ingests everything, runs detection and the alert checks. It takes a while the first time. If `Trizetto.zip` holds history that is no longer in the folder, unzip it into the mirror (`/data/dropbox/Trizetto Data/Trizetto/`) first.

## 4. Keeping it current

Nothing to do. While the dashboard runs it does one cycle every `REMITWATCH_SYNC_INTERVAL_MINUTES` (60 is a good value). A cycle is: list what changed in Dropbox, download it, ingest it, re-run detection if anything new arrived, run the alert checks, send new alerts, and send the summary report when one is due. Cycles are idempotent and cannot overlap.

For updates within a minute of an upload, add a webhook in the Dropbox app console (Settings, Webhooks): `https://YOUR-HOST/api/dropbox/webhook`. Dropbox verifies the address once and then calls it whenever a file changes. Calls are checked against the app secret.

The header of every page shows the newest payment date in the store and whether sync is on. The Data tab shows the last cycle and has a "Sync and analyse now" button. If no new remittance arrives for `REMITWATCH_STALE_DAYS` days, an alert says so; that usually means the TriZetto download or the Dropbox upload stopped, not that the payers stopped paying.

## 5. Alerts

After each cycle the last four weeks of payments are compared with the six months before them, per payer:

| alert | meaning |
|---|---|
| Ramping up | a payer is downcoding, bundling, denying or underpaying at twice its own recent rate or more, and the jump is too large to be chance |
| Climbing | the slow version: the last three months against the year before, for a rise too gradual to double in four weeks |
| Started again | a payer that was quiet for the whole six months is doing it again |
| New behavior | a payer and signal combination never seen before |
| One provider singled out | a payer downcodes one provider's level 4 and 5 visits far more often than everyone else's |
| Rate cut | a payer's usual allowed amount dropped on one or more codes |
| New reason code | a payer started using an adjustment reason it never used |
| Deadlines | open appealable money reaching its filing deadline within 30 days |
| Feed stopped | no new remittances |

New alerts go to `REPORT_TO` by email and to `ALERT_WEBHOOK_URL` if set (Slack and Teams incoming webhooks both accept the format). An alert stays active until the pattern stops, then clears itself. If the pattern returns, it is raised and sent again. Acknowledge an alert in the Alerts tab to mark it as seen.

## 6. Appeal packets from the visit note

Open a finding and use "Build the appeal from the visit note". Attach the note (PDF, Word or text) or paste it. The result is one PDF: the letter, the documentation review with word-for-word quotes, the remittance evidence and the note. It can be downloaded or emailed to an address in `APPEAL_EMAIL_DOMAINS`.

The review is done by Claude through the Anthropic API when `ANTHROPIC_API_KEY` is set. Before setting it:

1. Ask Anthropic for a BAA covering the API organisation (their privacy centre describes HIPAA-ready API access; the Messages API, which is all this uses, is covered).
2. Leave `REMITWATCH_NOTE_SCRUB=1`. Names, member and claim numbers known from the remittance, dates, phone numbers and similar patterns are removed from the note text before it is sent. This reduces exposure. It is not a guarantee of de-identification, which is why the BAA comes first.
3. Scanned PDFs have no text layer to clean, so they are not sent unless `REMITWATCH_NOTE_SEND_SCANS=1`. Paste the text instead.

Without a key the packet is still built, with the template letter and the note attached.

The review is written to be honest. When the note does not support the level billed it says so, recommends against the appeal, and none of its arguments go into the letter. This live call was not exercised during the build because no API key was available; the request shape follows the SDK documentation and is covered by a test with a stand-in client. Check the first few packets by hand.

## 7. Weekly routine for the billers (about an hour)

1. Alerts tab: read what changed. Acknowledge what you have seen.
2. Appeals queue: everything appealable or correctable, soonest deadline first.
3. Correctable errors (modifier 25 missing, 57 used on a 0 or 10 day global, missing modifier remarks): submit corrected claims from EMA, then mark the finding "appealed" so the later payment is credited to the fix.
4. Appeals: open the finding, build the packet from the visit note, send it through the payer portal or fax, mark "appealed".
5. When a payer upholds its decision, mark "lost" with a short note. When a payment arrives the finding is marked recovered automatically.
6. Progress tab: recovered dollars by month, the share recovered by payer, and the downcoding rate per payer over time.

Contract findings (Anthem commercial visits at 59 to 72 percent of Medicare, Medicare Advantage below Medicare) and payment fees go to the practice administrator, not the billers.

## 8. Filing windows

Each finding shows its filing date and where the window comes from. Read in the payer's own manual during the build: Anthem Kentucky commercial (two years from the remittance for the reconsideration, then 90 days for the appeal) and Anthem Kentucky Medicaid (90 days). Taken from research notes and not re-read: UnitedHealthcare 12 months, Humana 18 months, Aetna 180 days then 60, Cigna 180 days, Anthem Medicare Advantage 120 days then 30. Everything else uses a default by payer type. A participation agreement can set a different period, so treat the dates as estimates. To change a window, edit `APPEAL_RULES` in `remitwatch/codes.py`.

## 9. Updating

`git pull`, `docker compose build`, `docker compose up -d`. The store is migrated on start. Detection keeps every status, note and appeal date across runs, including when a new rule changes how a line is classified.

## 10. Troubleshooting

- `Dropbox files/list_folder failed 409 ... not_found`: `DROPBOX_ROOT_PATH` is wrong for this account, or the folder is in a team space (set `DROPBOX_PATH_ROOT_NS`).
- `cursor reset`: the cycle falls back to a full listing on its own.
- "another cycle is running" for a long time: a cycle holds a lock and refreshes it while it works. A lock with no sign of life for three hours is taken over by the next cycle.
- Unbalanced transactions in `stats`: a few payers send remittances that do not balance; the lines are still loaded and flagged.
- A payer name that lands in "Self-funded plan TPAs" or under its raw name: add a rule to `PAYER_RULES` in `remitwatch/codes.py` and run a cycle with `--force-detect`.
- Commands: `python -m remitwatch.cli cycle [--full] [--skip-sync] [--force-detect] [--report] [--no-email]`, `alerts [--notify]`, `detect`, `stats`, `serve`.
