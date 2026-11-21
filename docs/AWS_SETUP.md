# Moving RemitWatch to AWS

One small server in the practice's own AWS account, covered by AWS's business associate agreement (free). About an hour
of clicking, most of it waiting. Cost: about $30 to $40 a month (the server, its disk, its fixed address and the daily
backups), plus about $15 a year for the domain.

What the template builds: one server (2 vCPU, 4 GB) with an encrypted disk, a fixed public address, only HTTPS open (no
SSH: you reach it from the AWS console), automatic security updates, a snapshot of the disk every day kept for 14 days,
and automatic recovery if AWS's hardware fails. On first boot it installs Docker and starts RemitWatch from this
repository, with HTTPS from Let's Encrypt.

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

   It asks for each setting by the same name as on Vercel (have Vercel's **Settings, Environment Variables** open in
   another tab and copy each value across; press Enter to skip one). Secret values are not shown as you type. It makes
   the dashboard password and the scheduler secret for you if you leave them empty: write the password down.
3. When it asks **Move the store ... from the Vercel site now?**, answer **Y**. It copies the Vercel site's data from
   Dropbox (the findings, everything people marked, payer fax numbers, hidden reasons, appeal packets) and where the
   site had got to in the Dropbox folder, so nothing is downloaded again.
4. It starts the site and prints what is left: open `https://<the address>/setup`, change the Dropbox webhook to the new
   address, give ModMed the new key address (`https://<the address>/.well-known/jwks.json`), and turn the Vercel
   site off so two sites never send the same fax.

Run the script again any time to change a setting.

## After

- Backups: **EC2**, **Snapshots** shows one a day. To go back to one: make a volume from it and swap it in (or ask).
- Updates to RemitWatch: in Session Manager, `cd /opt/remitwatch && sudo git pull && sudo docker compose up -d --build`.
- Logs: `cd /opt/remitwatch && sudo docker compose logs --tail 200 app`.
- If the site does not answer: **EC2**, **Instances**, the RemitWatch server, **Instance state**, **Reboot**.
