# Moving RemitWatch to AWS

One small server in the practice's own AWS account, covered by AWS's business associate agreement (free). About an hour
of clicking, most of it waiting. Cost: about $30 to $40 a month (the server, its disk, its fixed address and the daily
backups), plus about $15 a year for the domain.

What the template builds: one server (2 vCPU, 4 GB) with an encrypted disk, a fixed public address, only HTTPS open (no
SSH: you reach it from the AWS console), system updates every night (the latest Amazon Linux release, with a reboot in
the same quiet hour when an update needs one), a snapshot of the disk every day kept for 14 days, and automatic recovery
if AWS's hardware fails. On first boot it installs Docker and starts RemitWatch from this repository, with HTTPS from
Let's Encrypt.

Steps 1, 2 and 3 are yours alone (an account, a legal agreement and a purchase in the practice's name). The rest a Claude
session on your own computer (the Claude desktop app) can do with you if you prefer.

## 1. The account (10 minutes)

1. Go to aws.amazon.com, **Create an AWS Account**: the practice's email, a card, and a phone check.
2. Sign in as the root user, open **IAM** (search box at the top), **Add MFA** for the root user, and set up an
   authenticator app. Keep the root user for this and billing only.
3. At the top right, pick the region **US East (Ohio) us-east-2** and keep it for every step below.

## 2. The business associate agreement (2 minutes)

Open **AWS Artifact** (search box), **Agreements**, **Account agreements**, **AWS Business Associate Addendum**: read it and
**Accept**. Do this before any patient data reaches the account. (EC2, its disks and snapshots, which the template uses,
are covered services.)

## 3. A domain (5 minutes; ready within minutes to a few hours)

Open **Route 53**, **Registered domains**, **Register domains**. Search for a name (it need not be pretty: staff bookmark
it, for example `yourpractice-billing.com`), buy it, and wait for the email confirming it. Route 53 makes a **hosted zone**
for it: open **Hosted zones**, click the domain, and copy its **Hosted zone ID** (it starts with Z).

The site's address will be a name under it, for example `remitwatch.yourpractice-billing.com`.

## 4. The server (5 minutes, then 10 minutes of waiting)

1. Download the template: on GitHub, open `deploy/aws/remitwatch.yaml` in this repository and click **Download raw file**.
2. Open **CloudFormation**, **Create stack**, **With new resources**, **Upload a template file**, choose the file, **Next**.
3. Stack name `remitwatch`. **SiteAddress**: the address (for example `remitwatch.yourpractice-billing.com`).
   **HostedZoneId**: the ID from step 3. Leave the rest. **Next**, **Next**.
4. Tick **I acknowledge that AWS CloudFormation might create IAM resources** and **Submit**.
5. Wait for **CREATE_COMPLETE** (about five minutes). The server then spends about ten more minutes installing itself.

## 5. The settings, and the data moved over (15 minutes)

1. In the stack, open the **Outputs** tab and click the link in **NextStep** (Session Manager), then **Start session**.
   A terminal opens in the browser.
2. Run:

   ```
   sudo /opt/remitwatch/deploy/aws/configure.sh
   ```

   It asks for the settings by the same names as on Vercel (have Vercel's **Settings, Environment Variables** open in
   another tab and copy each value across; press Enter to skip one). A few rare ones it does not ask for (some ModMed
   settings, the Vercel state folder's path, the tuning ones): it lists them at the end, to copy into
   `/opt/remitwatch/.env` by hand if Vercel has them. Nothing you type or paste for a secret is shown, the ModMed private
   key included (a dot per line shows it arrived), and a secret already set is only said to be set. It makes the
   dashboard password and the scheduler secret for you if you leave them empty, and shows the password once: write it
   down. Until a dashboard password is set the site serves no data at all. A password already in use that is shorter
   than 12 characters is kept when you press Enter (the site still accepts it, with a warning on `/setup`); it is
   replaced only when you answer yes, and then everyone signs in with the new one.
3. When it asks **Move the store ... from the Vercel site now?**, answer **Y**. It copies the Vercel site's data from
   Dropbox (the findings, everything people marked, payer fax numbers, hidden reasons, appeal packets) and where the
   site had got to in the Dropbox folder, so nothing is downloaded again. It asks only while the server's store is
   empty: once the server has its own data, the script never moves the Vercel site's data onto it again.
4. It starts the site and prints what is left: open `https://<the address>/setup`, change the Dropbox webhook to the new
   address, give ModMed the new key address (`https://<the address>/.well-known/jwks.json`), and turn the Vercel
   site off, so staff work on one site. Faxes go out from this server alone (`REMITWATCH_FAX_SENDER`,
   `docs/SERVER_RUNBOOK.md`), and only this server reads TriZetto.

Run the script again any time to change a setting. It waits for a data cycle, fax run or backup that is going to end (up
to 10 minutes), then starts the site again with the new settings. A setting changed in `/opt/remitwatch/.env` by hand
takes effect with `sudo docker compose up -d` (not `restart`).

## Never update or delete the stack

Once the server holds data, leave the CloudFormation stack alone: do not update it, and do not delete it. The store,
the uploads, the backups and the settings are on the server's own disk.

- **A stack made from the older template** (its description, on the stack's **Stack info** tab, says "automatic
  security updates"; the server running today was made from it) replaces the server with a new, empty one on almost
  any update: a change to **InstanceType**, **DiskSizeGiB**, **GitRef** or **SiteAddress**, uploading a newer template,
  or even an update with nothing changed once Amazon has published a newer Amazon Linux image (the template looks the
  image up again on every update). The fixed address and the DNS record then move to the empty server. The old
  server's disk is kept, but detached, and must be recovered by hand.
- **A stack made from the current template** (its description says "nightly operating system updates") keeps the
  server and its fixed address whatever the stack does: a parameter change or a newer image only changes what a new
  server would get, and the running server stays as it is. Deleting the stack leaves the server and its address
  running (they are kept, not deleted).

If an update is ever unavoidable, use **Create change set** first and read it: if the **Server** row says
**Replacement: True**, do not execute it.

What to do instead:

- **A bigger server**: **EC2**, **Instances**, the RemitWatch server, **Instance state**, **Stop**; then **Actions**,
  **Instance settings**, **Change instance type**; then **Start**. The site is down for a few minutes.
- **A bigger disk**: **EC2**, **Volumes**, the server's volume, **Modify volume**, a larger size. Then, in Session
  Manager: `sudo growpart /dev/nvme0n1 1 && sudo xfs_growfs -d /` (`lsblk` shows the disk's name if it differs).
- **New first-boot steps** (a later version of the template's user data): run them by hand on the server.
  `docs/SERVER_RUNBOOK.md` has the ones that matter, such as the nightly system updates.

## After

- Backups: **EC2**, **Snapshots** shows one a day. To go back to one: make a volume from it and swap it in (or ask).
- Updates to RemitWatch deploy themselves: every 5 minutes the server looks at main and deploys a new commit (build
  first, swap only when the build succeeds and the site answers; if it does not, the version before runs again). How it
  works and how to read its log: `docs/SERVER_RUNBOOK.md`, "Automatic deploys". `https://<the address>/api/health`
  shows the commit the server runs.
- Whoever can push to main controls this server, so protect main in GitHub (a required review, no force pushes):
  `docs/SERVER_RUNBOOK.md`, "Who controls the server".
- Logs: `cd /opt/remitwatch && sudo docker compose logs --tail 200 app`. Every command on the server starts with `sudo`:
  the Session Manager user is not root.
- If the site does not answer: **EC2**, **Instances**, the RemitWatch server, **Instance state**, **Reboot**.
