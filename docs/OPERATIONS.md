# Operations: set up once, then it runs itself

## 1. Where to run it

The store, the Dropbox mirror and the appeal packets hold patient information. Run it on a host covered by a business associate agreement (BAA).

Recommended: one small virtual machine in AWS, Google Cloud or Azure. For AWS, `docs/AWS_SETUP.md` walks through it with a
template that builds the server (`deploy/aws/remitwatch.yaml`), a script that asks for the settings
(`deploy/aws/configure.sh`), and `python -m remitwatch.cli import-cloud`, which moves a Vercel site's data over once.
`docs/SERVER_RUNBOOK.md` is the short version for whoever runs the server: the settings, the commands, the move and its
proof (`compare-cloud`), and the backups. All three sign a BAA at no extra charge (accept it in the account before loading data). Size it at 2 vCPU and 4 GB of memory, because a detection run on the full archive peaks at about 2 GB, with a 20 GB encrypted disk. That is roughly $25 to $35 a month. Install Docker and run `docker compose up -d`; the compose file starts the dashboard, its scheduler and Caddy for HTTPS.

Alternatives that cost more and need less administration: Fly.io with its compliance package, or Render on its HIPAA plan. Check current pricing; in November 2026 they were about $99 and $499 a month.

Vercel also works, and a push to `main` deploys there: the app keeps its store in a private Dropbox folder because Vercel has no disk. Setup and limits are in `VERCEL_SETUP.md`. Two things to weigh: a run needs about 1.4 GB of the 2 GB a standard Vercel function has (the archive adds about 3 KB per service line, so this lasts to around early 2028), and Vercel signs a BAA only on its Pro plan (a paid add-on) or Enterprise.

Not suitable: GitHub Actions for the scheduled job (no BAA), or a workstation that sleeps.

Four other agreements to confirm:

- Dropbox: the remittances already sit there. Dropbox signs a BAA on its team plans only.
- Email: alert and summary emails carry payer names, counts and dollars, never patient data. Appeal packets do carry patient data, so send them through a mail service under a BAA and keep `APPEAL_EMAIL_DOMAINS` set to the practice domain.
- Anthropic: the visit-note review (section 6) and the reading of payers' faxes. A fax that comes in is sent to Claude whole, as the payer sent it, with the patient's name and numbers on it.
- SRFax: appeal packets go out through it with the patient's name, member number and visit note. Sign its BAA before the `SRFAX_` settings are added.

The site serves no data until `REMITWATCH_PASSWORD` is set, on a server as on Vercel: every page goes to `/setup`, which says what to do. `REMITWATCH_ALLOW_OPEN=1` opens it without a password, for a developer's own machine only.

## 2. Dropbox app credentials (15 minutes, once)

1. Go to https://www.dropbox.com/developers/apps and create an app: Scoped access, Full Dropbox (the `Trizetto Data` folder is a shared folder mounted in the owner's Dropbox).
2. Permissions tab: enable `files.metadata.read` and `files.content.read`. Submit.
3. Settings tab: copy the App key and App secret into `.env` (`DROPBOX_APP_KEY`, `DROPBOX_APP_SECRET`).
4. Run `python -m remitwatch.cli oauth`, open the printed link while logged in as the Dropbox account that can see `Trizetto Data`, approve, paste the code. The command prints `DROPBOX_REFRESH_TOKEN=...`; put it in `.env`. Refresh tokens do not expire unless revoked.
5. If the folder is in a Dropbox team space rather than a personal account, set `DROPBOX_PATH_ROOT_NS` to the team root namespace id. Leave it empty otherwise.

## 3. First load (once)

A server moved from Vercel has nothing to load: `import-cloud` brought the whole store over (`docs/SERVER_RUNBOOK.md`). On a new host, start the site:

```bash
sudo docker compose up -d
```

Its first cycle starts about 45 seconds later and, with an empty store, mirrors the whole folder (about 112 MB of remittances), ingests everything, runs detection and the alert checks. It takes a while the first time. To run it again by hand, in the app's own container so the deploy and the app see it: `sudo docker compose exec app python -m remitwatch.cli cycle --full` (skipped while another cycle runs: try again when that one ends). If `Trizetto.zip` holds history that is no longer in the folder, unzip it into the mirror (`/data/dropbox/Trizetto Data/Trizetto/`) before the site's first start.

## 4. Keeping it current

Nothing to do. While the dashboard runs it does one cycle every `REMITWATCH_SYNC_INTERVAL_MINUTES` (60 is a good value). A cycle is: list what changed in Dropbox, download it, ingest it, re-run detection if anything new arrived, run the alert checks, send new alerts, and send the summary report when one is due. Cycles are idempotent and cannot overlap. When Dropbox fails part-way through a listing, the files already downloaded are still read in the same cycle. A spreadsheet in the folder that is none of the three weekly EMA exports (an A/R or a production report) is not read, and nothing from it is stored: the cycle's log says "Not read". It will be read once RemitWatch reads that report.

For updates within a minute of an upload, add a webhook in the Dropbox app console (Settings, Webhooks): `https://YOUR-HOST/api/dropbox/webhook`. Dropbox verifies the address once and then calls it whenever a file changes. Calls are checked against the app secret.

The header of every page shows the newest payment date in the store and whether sync is on. The Data tab shows the last cycle and has a "Sync and analyse now" button. If no new remittance arrives for `REMITWATCH_STALE_DAYS` days, an alert says so; that usually means the TriZetto download or the Dropbox upload stopped, not that the payers stopped paying.

Opening the dashboard also looks for new remittances when nothing has for `REMITWATCH_FRESHEN_HOURS` hours (3 by default): a run starts in the background (TriZetto, Dropbox, the analysis) and the page redraws itself when it has brought newer payments. Nobody has to press Sync.

### TriZetto direct

Remittances can come straight from TriZetto instead of through the Dropbox folder: the server reads TriZetto's SFTP server on every run, with a login TriZetto issues for RemitWatch, so a claim the payer paid leaves the lists within hours, with nobody copying files. The Dropbox folder can stay as it is (a file read from both is stored once). Only the server reads TriZetto. A Vercel site does not, even with the login set there, unless `TRIZETTO_SFTP_SERVERLESS=1`, which is only for a deployment with no server reading TriZetto with the same login: each site keeps its own hold on a refused password and its own expiry reminder.

What to ask TriZetto, word for word, is in `docs/TRIZETTO_REQUEST.md`: an email the owner sends the practice's TriZetto
administrator, with one written request inside it for TriZetto (no phone call). In short: a second SFTP login of its own
that gets a copy of every 835 (or a separate login such as a child Site ID, on the same terms), how ModMed collects the
practice's remittances, what the feed needs to run unattended, and the missing remittances re-sent to that login only.
Nothing in it may change what ModMed gets: not the existing SFTP password, not the Remittance Advice Settings ("Auto
Generate Remittances"), and nobody downloads or re-sends remittances in the portal, not even archived ones.

1. When TriZetto sends the login: `sudo bash /opt/remitwatch/deploy/aws/configure.sh`, TriZetto section:
   `TRIZETTO_SFTP_HOST` and `TRIZETTO_SFTP_PORT` (only if TriZetto names a server other than sftp.gatewayedi.com, or a
   port other than 22), `TRIZETTO_SFTP_USER`, `TRIZETTO_SFTP_PASSWORD`, how long the password lasts,
   `TRIZETTO_SFTP_DIRS` (the folder the 835 files are in) and `TRIZETTO_SFTP_HOSTKEY` (the fingerprints TriZetto gives:
   a server has one per key type, so paste them all, separated by commas; SHA256:... or the older MD5:... form, a
   key-type word in front is fine).
2. On `/setup`, press "Test the TriZetto connection" once: the server and its key, the sign-in, the files there and one
   remittance read.

It only reads: nothing on TriZetto's server is moved, renamed or deleted, and TriZetto's own text reports (.DAT) are not
opened (a download of one starts the next report). The server's key is kept the first time unless `TRIZETTO_SFTP_HOSTKEY`
is set; a different key afterwards stops the connection before the password is sent. A `TRIZETTO_SFTP_HOSTKEY` with no
fingerprint that can be read stops the feed before it connects. A server offers its favourite key type first: when that
is not the type listed or kept, its other key types are asked for before the key is called wrong. Every SFTP call has a
30-second limit, and a watchdog closes the connection at the run's deadline: the files read by then are kept, and the
next run reads the rest. The server has a fixed address (3.128.37.47), so an address TriZetto has to allow is no
obstacle.

The password. The feed signs in with the login TriZetto issued for RemitWatch. TriZetto's own SFTP login is usually the
Site ID, with one password for the account, and the practice's billing system (ModMed) may sign in with it: that login
is used only after ModMed has confirmed it is safe (`docs/TRIZETTO_REQUEST.md`, step 3). What protects the login:

- A refused password is not tried again for a day. TriZetto locks a login for an hour after repeated failed sign-ins, and
  a locked Site ID login would stop ModMed's claims and remittances too. After a refusal (TriZetto's own answer to the user
  name and password, however the server asks for it; a sign-in that timed out or a line that dropped before any answer is
  the network and holds nothing; a sign-in that worked but whose file service was refused ends a hold) nothing signs in
  with the same user name and password for 24 hours, then once more (TriZetto may have switched SFTP on since). Two
  sign-ins in a row that did not finish (a server may hang up instead of saying no) hold the feed for 6 hours, and Today
  says so. A typed-answer (keyboard-interactive) sign-in is given the password once; a notice that asks nothing is
  answered without it. A new password is tried at once.
- "Test the TriZetto connection" keeps to the hold too. While a hold is in place it does not sign in: it says "Not tried
  now" and until when, so pressing it again sends TriZetto nothing. It has no button to try anyway. A sign-in that passes
  in the test ends a hold, and a refusal there holds the scheduled runs too. The test always returns its steps.
- Today's data checks, and the test on `/setup`, say when a hold is in place. They say "refused the password" only when
  TriZetto refused the password itself (sent by the password method), "refused the sign-in" otherwise, and "would not take a password" when TriZetto's
  server asks for another way to sign in (then there is nothing to reset: TriZetto is asked to let the login use a
  password). The hold is kept on Vercel too (the test sets or ends it through the journal there). Only a salted, slow
  hash (scrypt) of the settings is kept, never the password.
- A reminder before it expires. TriZetto makes the SFTP password expire (every 60 days to a year, by vendors' accounts).
  configure.sh notes the day a new password is entered (`TRIZETTO_SFTP_PASSWORD_SET`) and asks how many days it lasts
  (`TRIZETTO_SFTP_PASSWORD_DAYS`). TriZetto's own words are understood: "90", "8 weeks", "6 months", "1 year",
  "quarterly" (90), "twice a year" (182); a number goes with the unit right after it, and a day count wins ("365 days
  (1 year)" is 365); 0 or "never" means no reminder. What cannot be read is asked for again, and a value already set that
  cannot be read is flagged on Today and `/setup` instead of being taken as "never". From 20 days before, Today's data
  checks say when it expires.
- A new password, when one is needed: the TriZetto administrator asks TriZetto in writing (the portal's Contact Us) to
  reset the RemitWatch login's password, or changes it only on a page that shows the RemitWatch login's username, never
  the Site ID's. Passwords go by phone or in person, never by email, to whoever runs the server, who enters it with
  configure.sh. If the feed uses the Site ID's own login, it is changed only together with ModMed, the same day.

### What EMA shows as paid

An item EMA shows paid is closed by the analysis: insurance payments in EMA's newest charge export, or on its ERA lines after the denial, on every code and date the item lists, each of at least half of what that code is expected to bring, where the claim has the code once on that date. A whole-claim item closes only when each of its codes is paid so: one code paid says nothing of the others, so fewer is a hint that names the codes EMA shows paid and the ones it does not. That covers a claim corrected and paid under a new number, a paper check, or a payment that never came as an 835. Its note says where the payment was seen; reopen it if that money is for something else. If a newer export no longer shows the payment, the item opens again. `REMITWATCH_EMA_CLOSES=0` turns it back into a hint.

### Written off in EMA, still workable

Each item carries what the biller gave up in EMA of the money it disputes (from the ERA & Payment Line Adjustments
export): rows written off, or moved on to the patient or the next payer, on the payer's own reasons for not paying.
EMA's routine posting of a contractual reduction (CO-45, CO-253, CO-59 and the like) is not giving anything up, unless
that reason is the item's own (CO-59 on a multiple-procedure reduction), and the patient's share (PR) is not the payer's:
neither counts. Reasons are read however EMA spaces them ("CO - 45"). A row is counted once though several exports repeat
it, a reversal cancels it (a reversal alone counts nothing; the direction is the one most of EMA's write-offs carry), the
amount is capped at the item's disputed amount, a whole-claim item counts only the codes and dates it lists, and where a
claim carries the same code twice on one date (EMA's rows are by claim, code and date) nothing is attributed. An item written off while its filing window is
still open is money given up that the payer can still be asked for: Today's plan lists them ("Work what EMA wrote off"),
the Worklist opens them with a filter chip ("Written off in EMA, still workable"), each row says "written off in EMA", and
the item says how much and since when. Work it as any other item, and reverse the write-off in EMA when the payer pays.

### Claims the payer never answered

EMA's exports say which claims went out; the remittances say which ones a payer answered. A claim EMA shows sent to a
payer is listed once 30 days have passed since it was sent (`REMITWATCH_NO_ANSWER_DAYS`) and the remittances on file
reach that day (a feed that stopped never makes newer claims look unanswered), when nothing answers it:

- no remittance for it (another submission's answer, from this payer or another, is not this claim's: the item says when
  one came; a later submission of the bill that was answered supersedes it);
- no ERA line posted in EMA for it (the payer's answer reached EMA even if its 835 never reached the site);
- nothing paid by insurance in EMA, by the newest export, net of anything taken back: the Insurance Payments column, or a
  Payment row whose activity or payment method says insurance (ERA, EFT). A Payment row that only names the payer could be
  the insurer's or a copay: the claim stays listed, and the item says EMA shows that payment. A voided payment is a payment
  taken back, never a voided charge;
- EMA's status is not denied (a denial is the payer's answer), paid, void, cancelled or in collections, and the claim
  was sent (not on hold, ready or unbilled);
- it is the latest submission of its bill that went out, by its number (C017, C024, ...): an earlier one sent again is
  not chased twice, and one on hold or not sent yet does not hide the one that went out. A later submission EMA's exports
  give no date for yet counts as sent the day after the bill's previous submission, its answer, or the denial EMA
  recorded ("Sent again after ...");
- when in doubt, a claim stays listed. A visit (same patient, date and code) leaves it only for a payment that still
  stands on that visit's own line under another bill (on its remittance, takebacks applied; in EMA's ERA lines; or EMA's
  Insurance Payments on that line), or when the same visit is chased under a newer bill (chased once). A denial of the
  same bill's earlier submission, or of an older bill, never stops the claim sent again from being chased.

These are "Chase the payer" items (Today's plan: "Chase claims the payer never answered"): look the claim up on the
payer's portal or call; not on file, send it again within the first-filing limit (see the filing window on the item);
rejected, correct and resend; denied or paid, get the remittance or EOB and work it here. A claim EMA calls rejected is
listed at once, under "Fix and resubmit". A claim still in EMA's print batch ("Batched for Printing") with no submission
date was never sent: it is listed under "Fix and resubmit" too ("In EMA's print batch, never sent"), once 30 days have
passed since the bill was made, dated by the first-filing limit: print and mail it, or send it electronically. A claim whose EMA status says closed or written off though no payer answered is
listed all the same, marked written off in EMA (no more than its charge, and not when the patient paid it; it is also
under "Work what EMA wrote off"); none of the statuses in the practice's exports so far says that. A claim known only
from the Claim & Claim Status report is listed too. With names shown, each shows the patient and the member ID from
EMA's rows (the claim's policy number; the name from a remittance for another claim of the same EMA patient, when there
is one); with names off, initials, as for every other item. Each is
sized by the payer's usual rate for its codes and units (a second and later surgical procedure at the payer's usual
share), and one charge listed by two export layouts counts once. The newest export decides a claim's status: a
spreadsheet's own creation time, or for a CSV the newest date among its rows.

Claims are read as far back as an item can still be open (the longest first-filing limit and appeal window); one sent
before the remittances on file begin is not judged. The date on each is the payer's limit for a first filing, counted from the date of service: Medicare's 1 year is the
regulation's (42 CFR 424.44); CHAMPVA's 1 year is too (38 CFR 17.276); VA community care claims (EMA's payer IDs VACCN
and TWVACCN, whatever name EMA gives the payer) take the VA network's 180 days, and so does a claim with no payer ID of
its own whose EMA payer name stands for the VA (at least 20 of that name's rows carry a payer ID, and 90 percent of those
are the VA's); other payers' limits are the common ones
and say so (the participation agreement's own limit applies). A payer the rules do not know is dated by the longest
common limit (1 year), so a guess never makes its item expire, and the item names 90 days as the date to send it by
where possible. A claim sent on time keeps its item after that date: the payer still owes a claim it has, and most take
proof of timely filing (EMA's submission date, or the clearinghouse's acceptance report) for one they have no record of,
so its date runs the payer's appeal window past that limit (marked not confirmed), and its note says to send it again by
the limit. A claim EMA gives no date for (no submission date, no bill date) is dated by the limit itself: the date of
service proves nothing about when it went out. Medicare's year from the date of service is firm, and a rejected claim never reached the payer. When a payer's remittances stop before
a claim's answer was due, or none from it is on file at all, the item says so: check its portal (and its ERA enrollment)
before sending the claim again. The notes recorded on one go to exactly two places: each item its claim's first remittance
brings, or the same bill's claim sent again to the same payer (its chase, or the items its first remittance brings). The
payer is judged by its name's family (Anthem, Humana, Medicaid, ...), which does not change between runs; within a group
that holds many payers (Medicare supplement carriers, the administrators of self-funded plans) only the payer's own name
counts, so Medicare's work never goes to a Medigap carrier's claim, and so it does for a payer the rules do not know (two
such payers can share a first word). VA community care is known by its payer ID whatever name EMA gives it, so its work
follows it to the VA's own remittance. Each starts To
do (Started if someone was working the claim), never Sent, Paid or Let go: the payer's answer is a new matter. The earlier
item stays (stale, out of every total) and, if its own claim id is listed again, comes back with its own status and notes.
One a person marked Paid stays counted as a recovery and hands nothing on. Another payer's claim on the bill or a
reprocessing months later never takes them, and any item a person wrote on is kept when it is no longer found.

EMA's claim statuses, as the Claim & Claim Status exports write them, are matched by words. A status that names a
rejection is listed under "Fix and resubmit". One that names a denial, a payment, a void, collections, a cancellation or
"resolved" (EMA's "Resolved/Paid" and "Resolved/Other", "Inactive/Voided") counts as answered or settled and is not
chased. One that names a hold, "ready" (EMA's "Ready for Posting"), unbilled, not sent, not submitted, a draft or a queue
counts as not sent yet; "Batched for Printing" with no submission date is the print batch above. Any other status (EMA's
"Payment Pending" or "Invalid", for example) counts as sent, and the claim is chased once nothing answers it. EMA's
"Claims State" column is a US state, never part of the status. The words are in `remitwatch/detect.py`
(`EMA_REJECTED`, `EMA_DENIED`, `EMA_SETTLED`, `EMA_GAVE_UP`, `EMA_NOT_SENT`, `EMA_PRINT_BATCH`), and
`tests/test_workable.py` pins how each of the practice's words is read.

These claims cannot be hidden for a payer (below): a claim nobody answered is not a reason a payer won't pay. Each is let
go one at a time, with a note. The site holds to it: Let go and Lost on a list leave them out and say so, and the item's
own Let go and Lost ask for the note. A request without a note, or one that lets them go in bulk, is refused and nothing
changes.

### Hiding a reason for a payer

When a payer is known never to pay something (a visit with modifier 57 at some payers), open one such item and click "Hide ... for (payer)", or pick the reason and the payer on a list and click "Hide ... for (payer)". Its open items go to Let go with the note typed, and so do new ones after each analysis. An item someone has started, sent or reopened stays as they left it. "Hidden" beside the list's count lists the rules, each with the number of items the rule itself let go (what "Show again" brings back; items a person let go by hand are not counted). "Show again" brings back what a rule hid. A rule removed this way is kept, with the day it was removed, so applying the journal again later changes nothing. Claims the payer never answered, and claims rejected before reaching it, are never hidden this way (`actions.NEVER_HIDDEN`).

### No appeal on old data

When the newest remittance is more than `REMITWATCH_STALE_APPEAL_DAYS` days old (14), Appeal by fax holds every item: it may have been paid since. Get the remittances current (TriZetto direct, or the Dropbox upload) and send again; "Still unpaid in EMA: fax it anyway" in the item, or "Fax them anyway" after a batch, sends them when someone has checked in EMA that they are still unpaid. The follow-ups that say a claim is unpaid (second requests and payment requests) wait the same way until the remittances are current; requests to the next level and records the payer asked for still go.

### The Providers tab

The same cycle rebuilds the provider tables after detection (`remitwatch/activity.py`; `python -m remitwatch.cli activity` rebuilds them alone). Five things decide how far the tab can be trusted:

- **Remittances lag the visit.** Half of all visits are paid within about three weeks and nine in ten within about five. The tab works out that curve from the store and treats a month of service as complete only when 97 percent of its visits should have been paid; the default window ends there.
- **A payer's remittances can go missing for a week or more.** The build looks for weeks in which a payer that pays every week sent next to nothing and then started again (and was not just as quiet in the same week a year earlier). It then measures the hole: for the months of service those weeks would have paid, the payer's claims are compared with its usual share of everyone else's, and a month more than a fifth short counts the difference as missing. A week the payer made up afterwards leaves no hole and is not listed. The weeks are on the Data tab ("Remittance weeks that look missing") and the months they touch are marked short on the Providers tab. Ask TriZetto for those files in writing (`docs/TRIZETTO_REQUEST.md`, point 4), never by downloading them in the portal: until they arrive every count on the site is missing those visits, downcoding included.
- **An EMA charge export only holds the visits that had become a bill when it was pulled.** Bills are written days after the visit, and how many days differs by clinician, so a week exported on its last day undercounts everyone and undercounts the slowest billers most. The tab reads the pull time from the workbook itself (the time EMA created the file), counts a day only when an export covering it was pulled late enough for that clinician's bills to exist, and lists the other days as waiting. To keep the recent-weeks panel current, export the full Encounter and Charge Lines layout (the one with Bill Creation Date, Bill ID and Patient ID) by date of service for the last six weeks each week. Overlapping exports are merged, and a later export moves a day's pull date forward. An export by transaction date, or one that holds only a sliver of a day another export holds in full, adds its charges but cannot vouch for the day; a day only such an export holds is left out until a proper export covers it.
- **Visits an insurer has decided, on the remittance side.** A self-pay or cosmetic visit never reaches a payer, and no remittance for VA community care has ever arrived in the folder. The EMA panel includes both. About 3 percent of visits name no clinician because the payer reported only the group; they are not spread evenly across clinicians.
- **One service is counted once, however many payers report it.** A second payer often sends its own line numbers or its own charge for the same billed line. The build folds every report into one service, keeps each payer's decisions apart (a payer is its payer id, whatever name it writes under), and takes as allowed the newest standing decision of the first payer to decide; a reversal cancels only the decision it matches. Each build records its checks with its other totals: visit codes per patient visit (close to 1), whether insurers paid more than was allowed across a family of services in any quarter, and what share of allowed sits on single services paid well above their allowed amount (under one percent on the archive).

How the measures are built, so they are not read for more than they say:

- A clinic day is a day with at least five patients seen. A half day counts as one, so compare patients a week as well as per day, and open a clinician's row for the weekday table.
- "Per clinic day, by state" uses the state of the Medicare contractor that paid that day's Medicare claims, which follows the office where the visit took place.
- A post-operative check, a light-box session or an injection given by staff on its own is not a patient seen by the clinician. Those are counted in their own column.
- "Kept from the year before" is shown three ways: everyone, established patients only, and patients who came back to the same clinician. First-time patients return far less often than established ones whoever sees them, so the first figure mostly reflects how many new patients a clinician had.
- Visit level is compared on established visits with no procedure the same day.
- "Skin cancer treated here per 100 first biopsies" counts only a first biopsy (none in the four months before, no skin cancer treated in the year before) and is a floor: treatment elsewhere never shows. It does not say whether a biopsy was well chosen. The pathology results do.
- Two columns follow each patient forward in time and so use fixed periods, printed under their tables, whatever months are chosen.

What the files cannot show: whether a light day was an empty schedule or patients who did not come (that needs the EMA appointment export, which nothing reads yet), what each clinician costs, and pay terms.

**Who can see it.** The tab compares named clinicians. By default everyone with the dashboard login sees it. To keep it for the owner, set `REMITWATCH_OWNER_PASSWORD` (12 or more plain letters and digits, different from the dashboard password). After that:

- The Providers tab shows a box asking for the owner's password. Nothing from that tab is sent to the browser until the right one is typed.
- The password is kept in that page's memory only. Pressing Lock (on the tab, or "Lock" in the page header from any tab; it closes the Providers and Calculator tabs), leaving the page, reloading or closing it, or half an hour without use closes it again. Press Lock before leaving a shared computer, and answer "never" if the browser offers to save the password.
- Everyone keeps the same login, so nobody is signed out or switched, and every other tab works as before.
- On Who and what and on Contracts, the number of lines billed (and the rate worked out from it) is left out wherever it would show how busy one clinician is: by provider, and in any other row that is three quarters or more one clinician's lines, such as a provider type or a code only one clinician bills. They show once the Providers tab has been opened on that page.
- Still visible to everyone, because it is the billers' work: findings and dollars at stake by provider; the dates of service on those findings, which show on which days a clinician worked; and the alert that says a payer is singling out one provider, which names how many of that provider's level 4 and 5 visits to that payer were downcoded.
- Ten wrong tries at the owner's password from one address close the provider figures to that address for ten minutes. The rest of the site is not affected. The count is per address, so someone in the same office can close the tab to the owner for a while by guessing; the owner can open it from another network.
- What it is not: encryption. It keeps the figures off the dashboard for people who have only the dashboard login. Anyone who can open the site's state folder in Dropbox, the remittance files themselves, or the host's settings is outside it. Keep the state folder where staff cannot open it, and have the owner enter the password in the host's settings personally.
- A weak owner password is not ignored: the figures stay closed to everyone until it is fixed, and `/setup` says why.

### The Calculator tab (owner only)

Opens with the owner's password, like Providers. Pick the Mohs surgeon, general dermatologist or APP average, or one clinician. "Plan 2027" is the default: the waterfall shows the collections now at the current year's rates, what Medicare's 2027 amounts and rules take from the same schedule, what your schedule changes, and what corrected contracts add. Move the levers under "The schedule"; the clinical outcomes (stages, closures, visit levels, lesions per patient) are under "How cases go" and are not targets. Under Contracts, choose how a contract is corrected and tick payers to see them renegotiated. "What a target takes" turns a yearly figure into what a clinic day has to bring in and how much more of each volume lever closes the gap.

The other views: Strategy lab (tick strategies; each line shows its collections, costs, visits and minutes; staging closures and bringing biopsies back are what-ifs marked as audit exposure, shown apart and never in a plan's headline), Practice figures (enter minutes, costs and pay plans and press Save; anything left empty is shown as not entered), Provider sheet (one clinician, not a role average; a pay target; printable). Pay and the Provider sheet count only the clinician's own production: radiation, drugs and pathology from their referrals never count toward a pay target, and the sheet's recipes are patient volume only (visits, new patients, clinic days, weeks, Mohs cases).

The ASC card (Strategy lab) starts from where each surgeon closes today. Enter the practice's share of the ASC's facility fee (100% if the practice owns all of it) and, when known, the ASC's cost per case; until then the card shows the owned and not-owned views side by side and the plan counts the lower one. Mohs is never moved to the ASC. 'Kentucky closures to the Newburgh ASC' is off (0%) until a share is set; it is a 'check first' option: the setting must follow each patient's needs, and the practice should confirm the ASC safe-harbor tests with counsel before scaling it. The Coding exposure panel on the Calculator view shows each clinician's Medicare rate of visits billed with modifier 57 or 25 beside Mohs and biopsies; a high modifier-57 rate is a reason for an internal coding review.

The ASC final rule view is about the practice as a whole (the clinician picked above does not change it). It takes every case the surgeons billed at the ASC in the window (a closure after a Mohs the same day, or an excision with its repair, on a claim with the ASC's place of service), a year at that pace, and prices the ASC's facility fee at Medicare's October 2026 rates and at the CY 2027 final rule, wage-adjusted for Warrick County. Medicare is priced exactly, with Medicare's own payment after the 2% sequestration and the patients' coinsurance shown apart. The ASC's own remittances are not in the feed, so each other kind of payer is taken at a share of Medicare's ASC rate that starts at what that kind pays the surgeons against Medicare for the same procedures: type the ASC contract's real share where you know it, and untick "Follows Medicare's yearly rates" for a contract with fixed prices (Medicaid and other payers start unticked). Enter the practice's share of the ASC to see the practice's part. The same cases' surgeon fees for Medicare and Medicare Advantage are shown beside them (the 2027 physician fee schedule). A Medicare Advantage plan unticked on the Calculator ("moves with Medicare in 2027") keeps its 2026 surgeon fees. On a copy of the figures built before the provider tables kept the ASC's cases, the view says so and prices nothing until the next rebuild (Sync and analyse now). Below is the ASC rate calculator: pick a state and county, type codes, the start of a code or a range. The rates are CMS's own, as CMS posted them on November 10, 2026, worked out as ASCA's calculator works them out. Skin substitute supplies (indicator S2) are not wage-adjusted, as CMS pays them. Settings on this view last for the visit; the lab's ASC card is where they are saved.

Each year, when CMS posts the final rule's ASC addenda (usually November), or posts them again with corrections, refresh the tables from CMS's own files: the rule year's Addenda AA, BB, DD1 and FF, the latest quarter's AA and BB for the year before (from CMS's ASC payment rates addenda page), and the rule year's ASC wage index county-to-CBSA crosswalk. Run `python scripts/build_ascfs.py --cms <rule year>=AA.csv,BB.csv --cms <year before>=AA.csv,BB.csv --counties <crosswalk.csv> --dd1 <DD1.csv> --ff <FF.csv> --released <the day CMS posted them, YYYY-MM-DD>`. The script compares what it wrote with CMS's files, code by code and county by county, and changes nothing if one differs. ASCA's workbook can be given with `--asca` as well; it is checked the same way (its version 77 was behind CMS's November 10, 2026 update by 68 codes and 5 counties). It rewrites `remitwatch/ascfs.py` and `remitwatch/cms/`. Run the tests, commit, push. The physician fee schedule for the same year comes from `scripts/build_mpfs.py`.

Practice figures and scenarios are stored only when an owner password is set (`REMITWATCH_OWNER_PASSWORD`); without one, those two routes refuse. A save carries the version it read: if another window saved in between, the page reads the figures again, applies its own change on top and says so, so neither window's work is lost. If the figures cannot be read, Save stays off until they are read again.

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

### Connecting ModMed EMA

With EMA connected, the visit note behind an appeal is read straight from EMA instead of being found and uploaded by hand: "Get the note from EMA and build the packet" in an item's drawer, and "Build packets from EMA (next 10)" on the Worklist when "Appeal with the visit note" is chosen (it works through the open items still inside their windows, soonest first, about a minute each). It reads only: nothing is written to EMA. It uses ModMed's ONC-certified FHIR API, which ModMed offers at no charge.

1. Make the app's key on any computer with the code: `python -m remitwatch.cli modmed-key`. Put the private key it prints in the host's settings as `MODMED_PRIVATE_KEY` (all of it, BEGIN and END lines included; line breaks may be written as `\n`) and redeploy.
2. Register the app with ModMed at portal.api.modmed.com as a Bulk FHIR (backend) application. Give ModMed the public key printed with the private one, or, where the form asks for a JWKS URL, `https://<the site>/.well-known/jwks.json`. Note the client ID ModMed gives the app.
3. In EMA: Admin, Manage Bulk FHIR, add that client ID. This is the practice's consent.
4. Set `MODMED_CLIENT_ID` and redeploy. On `/setup`, press "Test the ModMed connection": every step (key, discovery, sign-in, server, search) says what works.

How a note is found. The patient first: EMA's own patient ID on the claim's EMA rows, then the medical record number the payer echoed, and only when those find nobody, the patients with the claim's name. A note goes into an appeal by itself only when exactly one patient is identified by two of the claim's identifiers that agree: EMA's patient ID or the record number, together with the full name (family name and first name). A name alone, or a name and a date, never sends a note: two patients can share a name and a visit day. Such a match waits in the item for a person to confirm it. The member ID is not used to identify a patient: a payer can give one ID to a whole family, and the 835 has no birth date, so it cannot tell a parent from a child of the same name. Then the note: that patient's signed, current notes for the date of service (tied to a visit that day, or dated that day). More than one: the rendering clinician's (by NPI), then a progress note before other kinds, else a person picks. Drafts and notes entered in error are never used. Where ModMed lets the app export but not search, the same lookups run over a Bulk FHIR export (set `MODMED_GROUP_ID` if ModMed gives a group to export from).

When a person has to choose, the item lists the notes to pick from, grouped as Patient 1 and Patient 2 when more than one patient fits the claim. With names shown each group gives the patient's name, birth date and EMA IDs, and which of the claim's identifiers agree with that patient. With names off and two patients fitting, there is nothing to pick: the item says to attach the note by hand. A later note that names no visit, a document that is not a visit note, or a file that does not read is marked "check first: may not be this visit's note". A packet says what identified the patient ("patient identified by EMA patient ID and name", or "the note chosen by a person").

If ModMed's registration differs: `MODMED_CLIENT_SECRET` and `MODMED_API_KEY` for a client-secret app; `MODMED_FHIR_BASE`, `MODMED_TOKEN_URL`, `MODMED_SCOPE`, `MODMED_AUDIENCE` and `MODMED_KEY_ID` where ModMed names other values. On the server, `sudo docker compose exec app python -m remitwatch.cli prepare-appeals --limit 25` does what the Worklist button does: the EMA queue when ModMed is connected, otherwise the notes uploaded by hand.


Open a finding and use "Build the appeal from the visit note". Attach the note (PDF, Word or text) or paste it. The result is one PDF: the letter, the documentation review with word-for-word quotes, the remittance evidence and the note. It can be downloaded or emailed to an address in `APPEAL_EMAIL_DOMAINS`.

The review is done by Claude through the Anthropic API when `ANTHROPIC_API_KEY` is set. The practice's business associate agreement with Anthropic is signed (November 2026) and covers it.

1. `REMITWATCH_NOTE_SCRUB=1` (the default) removes names, member and claim numbers known from the remittance, dates, phone numbers and similar patterns from the note text before it is sent, so less leaves the site than it needs to.
2. Scanned PDFs have no text layer to clean. Set `REMITWATCH_NOTE_SEND_SCANS=1` to have them reviewed as they are.

Without a key the packet is still built, with the template letter and the note attached.

The review is written to be honest. When the note does not support the level billed it says so, recommends against the appeal, and none of its arguments go into the letter. This live call was not exercised during the build because no API key was available; the request shape follows the SDK documentation and is covered by a test with a stand-in client. Check the first few packets by hand.

### Until ModMed's API is approved: EMA by hand (the Upload page, on the server)

While ModMed's approval of the Bulk FHIR app and the synapSYS application is pending, the practice brings EMA's side in by
hand. On the server, open **Upload from EMA** at the top of the dashboard (`https://<the site>/upload`; the same login).
The page has its own guide; in short:

1. **Every week (say Monday), three reports from EMA, the last six weeks by date of service, saved as Excel:**
   **Encounter & Charge Lines** in the full layout (Service Date, Patient ID, Bill ID, Bill Creation Date, Claim ID, Bill
   Item Identifier, CPT/Product, Modifiers, Units, Charges, Insurance Payments, Activity Type, Primary Provider, Payer,
   Location); **ERA & Payment Line Adjustments** (Claim ID, CPT, Service Date, Reason Code, RARC Code, Adjustment Status and
   Subtype, Adjustments, Payments, Transaction Date); **Claim & Claim Status** with the Patient ID column added. These are
   the reports the practice already saves to the Dropbox folder. Drop the three files on the page (several at once, or a
   .zip). CSV works too; Excel is best.
2. **Visit notes, as items need them:** the page lists the open items that rest on the visit note ("Visit notes appeals are
   waiting for"). For each, open the patient's visit in EMA on that date of service and save the signed note as a PDF;
   drop the PDFs on the page together. Each is matched to its visit by the patient's name and the date of service it
   states (a scanned page has no text: use the row's Attach note button). A note that also states the EMA Patient ID (or
   the record number) goes into the appeal by itself; one that matches only by name and date waits for a person (confirm
   it in the item, or use Attach note). Then the appeal goes as it would with a note from EMA's API: "Use the note
   uploaded from EMA" in the item, "Build packets from the uploaded notes" on the Worklist, or Appeal everything
   appealable.
3. **When remittances stop coming:** tell the owner. The missing ERA (835) files are asked for in writing: the TriZetto
   administrator asks TriZetto (the portal's Contact Us) to re-send them to the RemitWatch login
   (`docs/TRIZETTO_REQUEST.md`, point 4). Nobody downloads them in the portal, not even archived ones: that can send them
   to ModMed a second time. ERA files the practice already has can be dropped on the page.

What happens: each export and remittance file is kept in the upload folder beside the store (`/data/uploads`) and read by
the next cycle (it starts within about a minute of an upload) with the same loader as a file from Dropbox; the worklist
follows when that cycle's analysis finishes. Each upload says what was in the file, what was new, what RemitWatch had
already, and what it left out and why (the report's title lines, a totals line, a row repeated exactly, columns it does
not use, columns it needs that are missing). Uploading the same file again changes nothing. A spreadsheet that is none of
the three exports (an A/R or production report, say) is refused with the reason, and not kept. One upload can be up to
240 MB, an EMA export up to 60 MB (export a shorter date range for a larger one), a visit note up to 15 MB. A note is kept
the same way and becomes one more place the appeal's note lookup searches, ahead of EMA's API, with the same matching
rules (How a note is found, above). Taking a note back (the wrong file, or the wrong visit): a packet already built from
it stays with its item for the record, but it is not faxed from then on; the next appeal builds it again from the note in
its place. An export or a remittance file never leaves the server. A visit note leaves it only inside the appeal packet
built from it (faxed to the payer, or emailed where the practice allows it) and for the note review. Uploads are a
server's: on the Vercel site the page says so, and the exports keep going to the Dropbox folder.

### Faxing appeals

With a fax line connected, an appeal goes to the payer in one click and is chased until the claim is paid or decided.

**One click.** On any list (the Worklist, a claim page), tick the items, or the box at the top of the list for all of them, and press **Appeal by fax**. Or press **Appeal everything appealable** at the top of the Worklist (or the Faxes page): every open item an appeal letter settles (the visit note, the coding rules, or the payer's own rate), inside its filing window and not faxed yet. For each item the site:

1. reads the visit note from EMA where the appeal rests on it (a medical necessity or level denial, a bundled visit, a bundled procedure);
2. has Claude review the note and write the paragraphs that justify the claim, quoting the note;
3. writes the letter (the practice's details, the patient, the member and claim numbers, the reason codes, the rule or the rate), puts a cover sheet with no patient named on it in front, and the note and the remittance evidence behind;
4. faxes it to the payer's appeal number and marks the item Sent.

An item that cannot go is held, with why:

- no fax number saved for the payer yet (type it in the box that appears; the held items go as soon as it is saved);
- the visit note needs a person to pick it or attach it: more than one note that day, only the name agrees with the claim, two patients fit, or the only document is a later note (How a note is found, above); or no signed note yet;
- Claude's review says the note does not support it (it is then counted in Suggested templates on Bigger wins), or says to correct the claim and send it again instead;
- Claude's review has doubts: it says to appeal only with caution, or finds the note supports the code billed only in part or not at all, or supports a lower code. Such a letter would tell the payer the practice concedes it, so it is never faxed by itself: open the packet, and send it by hand only if a person decides it should go;
- Claude's paragraphs quote words the note does not have, or the quotes could not be checked: open the packet and check it against the note;
- the remittance does not say the payer changed the code (the higher level is inferred from the charge, or from EMA's charge): check in EMA that the higher code went out, then press "It went out as billed: fax it";
- the remittances are more than two weeks behind (No appeal on old data, above): "Still unpaid in EMA: fax it anyway";
- the letter still has blanks, the practice's details are missing, or this site is not the one that sends faxes.

Items fixed in EMA and sent again as a claim, and items a person has to decide, are never faxed.

**Setting up the line (once).** The service is SRFax (srfax.com): HIPAA plans, a business associate agreement for healthcare accounts, sending and receiving by an API. Sign up for a plan with a fax number, sign its business associate agreement, then set in the host's settings and redeploy:

| Setting | What |
|---|---|
| `SRFAX_ACCESS_ID` | the SRFax account number |
| `SRFAX_ACCESS_PWD` | the SRFax account password |
| `SRFAX_CALLER_ID` | the fax number SRFax gives the account. Letters give it as the practice's fax, so payers fax their answers to it and the site reads them |
| `SRFAX_SENDER_EMAIL` | the address SRFax sends its own delivery notices to |

Also set every `PRACTICE_` detail (name, address, tax ID, group NPI, phone, email): no letter is faxed with a bracketed blank in it. On `/setup`, press "Test the fax line".

**One site sends.** `REMITWATCH_FAX_SENDER` names the site that faxes: `server` (the default, also when it is not set) or `serverless` (the Vercel site); `none` pauses faxing. The SRFax settings may be on both sites: only the one named sends, and the other shows the Faxes page and opens the faxes that came in. `/setup` says which site sends, and marks a value that names no site as a fault. With the practice's server running, leave the setting out on both sites: the server sends.

**Payer fax numbers.** Each payer's appeal fax number is typed once, on the Faxes page or in the box that appears when an item is held for it, from the payer's provider manual, its appeal form or one of its letters. A second-level number can be saved too (Medicare's second level is a different contractor, so for Medicare it must be: without one, Medicare's second-level follow-ups wait; other payers' go to the appeal number). The site faxes only to numbers a person saved: a fax with a patient's record sent to a wrong number is a breach. So:

- Adding or changing a number takes the owner's password, where one is set (`REMITWATCH_OWNER_PASSWORD`). The page reads the number back before it is saved.
- Who saved each number and when is kept, with the number it replaced (the history shows on the Faxes page).
- A number that replaced another is used by retries and follow-ups only once someone confirms it (Confirm, on the Faxes page). A first appeal pressed by a person uses it at once.
- An item whose last fax at a level went to another number waits for a person, who sends it in one click ("Send it to ..." under For a person).

**What happens next, by itself.** On the server every cycle (hourly) does this after the analysis; on the Vercel site, when it is the one that sends, a daily call at 15:43 UTC does. No cron is needed. `sudo docker compose exec app python -m remitwatch.cli fax-cycle` does the same by hand. Fax runs never overlap: one that finds another going skips it (`fax-cycle` then exits 1).

- It checks each fax still going out. A fax the payer's line did not take (busy, no answer) is sent again, three tries in all, to the number saved for it now; after that the Faxes page asks a person to check the number. A send cut off midway (a deploy, a crash) is checked against SRFax's list of sent faxes before anything is sent again; when SRFax cannot say, the Faxes page shows it as not settled and a person says whether it went out.
- It reads each fax that came in with Claude (what it is, the decision, claim and member numbers, the patient, dates, procedure codes, any deadline). A fax is tied to an item by itself only on a number that names the claim (the practice's claim ID, the payer's claim number, the member ID with the date of service, or the bill, the claim ID's first nine characters, with the patient's family name), and only when it came from a number known for that payer (one saved for it, or one a person tied a fax from before). A letter that names procedure codes answers those lines; one that names none answers every item of the claim. Anything else is listed on the Faxes page as "Needs a person to match", with the items it may answer: open the item it answers and use "Tie a fax that came in to this item", which can also say what the fax decides. A fax Claude could not read is read again, three tries in all. A line goes into the item's history.
- It sends the follow-ups that are due. No decision after the payer's time to answer (60 days for Medicare and Medicare Advantage, 30 for commercial plans) gets a second request, then another every week. A denial upheld goes to the payer's next level, with its decision attached, to the second-level number. A request for records gets the packet again, and the payer's time to decide starts again from it. An approval not paid within 30 days gets a payment request, then weekly. Twelve unanswered requests at one level, twelve payment requests, a second level upheld, or a follow-up the payer's line never took (three tries) go to a person (a call, a complaint to the Department of Insurance, an independent review, the number checked). Second requests and payment requests wait while the remittances are more than two weeks behind, and every follow-up waits while the practice's details have a blank. A payment closes the item and ends the follow-ups.

The Faxes page shows all of it: the follow-ups due (with a button to send them now), the payer numbers, what came in and what went out, each with its PDF. On the server, `sudo docker compose exec app python -m remitwatch.cli send-appeals` does what Appeal everything appealable does.

**Suggested templates.** Each note Claude's review found short is kept, by code: what the note lacked and what to document next time. Bigger wins lists them, with what the note for that kind of service must carry; "Write the EMA template" has Claude turn them into the fields an EMA template should make required, the prompt each field gives the clinician, and the line the clinician signs. Only the code, the payers and the gaps go to Claude for that, nothing about a patient. Which clinicians' notes fell short is shown only where the Providers tab would show it.

## 7. Daily and weekly routine

Every day (whoever works the claims):
1. Open the site. It opens on Today. If a yellow "Before you start" line is at the top, the numbers are behind (usually no new remittance files): tell the billing lead, then carry on.
2. Work the plan from the top. Step 1 is always what closes within seven days. Press its button: the Worklist opens with exactly those items, soonest first.
3. Open an item. The box at the top says what happened, what to do, what you need and the date. Do it in EMA or the payer's portal, then press "Mark sent and open the next". Use "Started" for something you began and will finish later, "Let go" for something not worth pursuing, and "Lost" when the payer upholds its decision. A claim the payer never answered, or one rejected before it reached the payer, is let go or marked lost one at a time, with a note saying why. Paid is set by itself when a later remittance pays.
4. When step 1 is done, clear a batch: one kind of work for one payer (for example "Ask for the usual rate, Humana Medicare Advantage"), so one portal and one kind of letter cover the pile. About a fifth of the items are worth less than $20 each and together a few percent of the money; the Worklist's amount filter hides them. An item whose amount reads "not known" has no usual rate to compare with and can be large, so it is never hidden.
5. Follow-ups appear on Today as their own step once something sent 30 days ago is still unpaid.
6. To look up one claim, type its claim ID (as EMA shows it; the start of it is enough) or the payer's claim number in "Find a claim" at the top. It opens every remittance the payers sent for that bill, oldest first, line by line with every adjustment and remark in words, whatever its status, plus the EMA rows; "Export every line" saves it as a spreadsheet. From an item, "See the whole claim" does the same.
7. The category tabs (Denials, Bundling & modifiers, Downcoding, Contracts & underpayment) show the same work by category. The red number on a tab is what closes there within 7 days. On each page the boxes at the top filter the list (closes in 7 days, window open, to decide, sent, window closed); a line under "What to do" lists one kind of work, and clicking it lists those items and the reasons behind them. "Start with what closes first" opens the first item. A click on the red number opens just those items. Fees & takebacks opens on the takebacks of the last 90 days, to check against the payer's letter; "Payment fees" lists each payer that took fees, and opening one gives its letter asking for direct deposit with no fee. Send it, then press "Mark asked".

Every week (practice administrator or owner, about 20 minutes): the Data tab's checklist (stale feed, missing remittance weeks, EMA exports that miss visits, filing windows not confirmed), then Bigger wins: payer rates against Medicare for the next contract talk, denial causes that keep coming back (each says who fixes it), fees set below what a payer allows, and payment fees.

The steps below describe the same work in more detail. The earlier Appeals queue is now the Worklist (its old address opens it), and the analysis pages are under More.

1. Alerts (under More, and the Alerts button at the top): read what changed. Acknowledge what you have seen.
2. Worklist: everything appealable or correctable, soonest deadline first, 20 at a time with "Show 20 more". Each row says who it is about. With `REMITWATCH_SHOW_PHI=1` that is the patient's name and member number, the search box finds a patient by name or member number, and clicking a name lists everything in the queue for that patient (the same name and member number; a patient with two insurers appears once for each, and the name search finds both). With it off (the default) the row shows initials, and the claim ID beside them is the Claim ID to search for in EMA. Export CSV gives the same list for a spreadsheet, with the patient columns only when names are on.
3. Correctable errors (modifier 25 missing, 57 used on a 0 or 10 day global, missing modifier remarks): submit corrected claims from EMA, then mark the finding "appealed" so the later payment is credited to the fix.
4. Appeals: open the finding, build the packet from the visit note, send it through the payer portal or fax, mark "appealed".
5. When a payer upholds its decision, mark "lost" with a short note. When a payment arrives the finding is marked recovered automatically: on the same claim, on a corrected claim under a new bill number, or from another payer, for the same patient, date, code and units. The Status menu at the top shows what was resolved, and the finding says how. When the match is close but not certain (a different member number, a code that is often paid twice in a day such as a Mohs stage, fewer units, a payment made before the denial, the service processed to the patient's deductible, or a payment EMA shows but no remittance does), the finding stays open with a yellow note under its title, for example "Possibly paid on claim ...". Check that claim; if it paid this service, mark the finding recovered. If the site closed something it should not have, set it back: once you change a finding the site closed, it stays as you set it. A payment the site finds on a finding you marked lost or dismissed shows as a yellow note and does not change your status. A payment the payer took back no longer counts. A payment only counts once its remittance is in the Dropbox folder (or the EMA exports are): when the newest remittance there is more than ten days old, every findings table says so, because anything paid since then still shows as open. For what is still unpaid today, the A/R report in EMA is the record; this site's value is the money A/R does not show (underpayments, downcodes, bundling, and denials that were written off).
6. Progress tab: recovered dollars by month, the share recovered by payer, and the downcoding rate per payer over time.

Underpayments (the list on the Contracts & underpayment tab) come in two kinds. A line paid below the payer's own usual rate for that code is its own finding: appeal it like any other. When the payer paid the same lower amount for the code on five or more lines and the remittances point to a reason (85 percent on PA or NP visits, half for a second procedure that day, mostly the Indiana office, one plan, or a rate change from a date), those lines are one SECOND_RATE finding with the claims listed and the reason. Check it against the contract, then ask the payer once about all of them. Each claim keeps its own filing deadline. The row shows the range of dates, and the finding says how many claims are still inside their windows and when the next one closes; start with the patterns where that date is close. Mark it in progress when you ask, and recovered or lost when the payer answers.

Contract findings (Anthem commercial visits at 59 to 72 percent of Medicare, Medicare Advantage below Medicare) and payment fees go to the practice administrator, not the billers.

## 8. Filing windows

Each finding shows its filing date and where the window comes from. Read in the payer's own current manual (checked November 2026): Anthem commercial in Kentucky and Indiana (two years from the remittance for the reconsideration, then 90 days for the appeal), Anthem Kentucky Medicaid (90 days), Anthem Medicare Advantage (120 days from the remittance, then 30 days from the reconsideration letter), Humana (18 months from receiving the original determination, counted from the remittance date), UnitedHealthcare (12 months from the original remittance for reconsideration and appeal together) and Medicare Part B (120 days for a redetermination). Humana Medicaid uses 120 days, marked not confirmed: Humana's Kentucky plan takes an appeal of a denial or reduction within 120 days (KRS 205.534) and a contracted-rate dispute within 24 months, its Indiana plan allows 60 days, and the remittance does not say which plan the member is in. Not yet re-read: Aetna 180 days then 60, Cigna 180 days, the Federal Employee Program, and the payer-type defaults.

A claim fixed and sent again runs on a different clock from an appeal. Where the payer publishes it, the finding's date is that clock: Medicare Part B takes no corrected claims, so a clerical error (a wrong or missing modifier or code) is fixed by a reopening within one year of the remittance, information or records the claim lacked go in a redetermination within 120 days (a reopening takes no documents), and a claim returned as unprocessable (remark MA130, on the line or the claim) needs a new claim within one calendar year of the date of service; the instructions on the item say which; Humana Medicare Advantage takes a corrected claim within one year (its tip sheet counts from the original determination, its manual's filing limit from the date of service, so the earlier date is shown); Anthem Medicare Advantage within 365 days of the date of service; Humana's Kentucky Medicaid plan within 365 days of the date of service (marked not confirmed, because the remittance does not say whether the member is in the Kentucky or the Indiana plan, and Indiana's limits are shorter). For other payers the participation agreement sets the corrected-claim limit, usually counted from the date of service and often 90 days, so the finding keeps the appeal date, says so, and counts on the Data tab as resting on a window not yet confirmed. UHC Integrated Services (Wausau) is treated like UMR: UnitedHealthcare's 12 months, not confirmed for the plan itself. A participation agreement can set different periods for anything here, so treat the dates as estimates. To change a window, edit `APPEAL_RULES` or `CORRECTED_RULES` in `remitwatch/codes.py`.

## 9. Updating

The AWS server updates itself: a merge to main is deployed within about 5 minutes (`docs/SERVER_RUNBOOK.md`, "Automatic deploys"). On another host: `git pull`, then `REMITWATCH_COMMIT=$(git rev-parse HEAD) docker compose build` (with the commit, `/api/health` says what runs; a plain `docker compose build` shows `"commit": null`), then `docker compose up -d`, each with `sudo` where Docker is root's. The store is migrated on start. Detection keeps every status, note and appeal date across runs, including when a new rule changes how a line is classified.

## 10. Troubleshooting

- `Dropbox files/list_folder failed 409 ... not_found`: `DROPBOX_ROOT_PATH` is wrong for this account, or the folder is in a team space (set `DROPBOX_PATH_ROOT_NS`).
- `cursor reset`: the cycle falls back to a full listing on its own.
- "another cycle is running" for a long time: a cycle holds a lock and refreshes it while it works. A stop of the app ends its cycle and frees the lock, and a start frees the locks of runs that ended with the last container. A lock with no sign of life for three hours is taken over by the next cycle.
- Unbalanced transactions in `stats`: a few payers send remittances that do not balance; the lines are still loaded and flagged.
- A payer name that lands in "Self-funded plan TPAs" or under its raw name: add a rule to `PAYER_RULES` in `remitwatch/codes.py` and run a cycle with `--force-detect`.
- Commands (on the server, each after `sudo docker compose exec app`): `python -m remitwatch.cli cycle [--full] [--skip-sync] [--force-detect] [--report] [--no-email]`, `alerts [--notify]`, `detect`, `activity`, `stats`, `backup`, `fax-cycle`, `send-appeals`, `prepare-appeals`, `modmed-check`, `serve`. `README.md` lists them all. `import-cloud` and `compare-cloud` run with the app stopped, as `docs/SERVER_RUNBOOK.md` shows.
