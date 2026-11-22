# TriZetto request: the email to send

The practice owner sends the email below to the practice's TriZetto administrator. Before sending it, the owner fills in
the brackets marked OWNER (the fee limits TriZetto may go ahead within, and where an order form goes). The administrator
fills in the other brackets, sends TriZetto the written request inside it (the portal's Contact Us link, or
physiciansupport@cognizant.com from the account's email address), and replies once with TriZetto's written answer and the
form at the end. No phone call; passwords only by phone or in person; no patient data by email.

What it gets: a login RemitWatch reads TriZetto's SFTP server with on its own, every hour, with nobody downloading files
(`remitwatch/trizetto.py`), plus what the feed needs to run unattended (password expiry, the server's key fingerprints,
folders, retention, IP rules), the missing remittances re-sent into that login only, and the ERA enrollment status.
Everything in it protects ModMed, which gets the practice's claims and remittances through the same TriZetto account.

```text
Subject: TriZetto: one written request for our automatic remittance feed

Hi,

We're connecting our reporting dashboard (RemitWatch, our own system on our own server) straight to TriZetto, so our payers' remittance files (835s, also called ERAs) flow into it automatically and nobody has to download anything. TriZetto has to issue it a login first. Here is what I need from you. It's all in writing, no phone call.

1. Send TriZetto the request below, as one message. Use the "Contact Us" link inside the TriZetto portal (top right) if you can, because TriZetto then knows the request comes from our account. After pasting, check that the last lines (Thank you, your name, our practice name) are still in the box; if anything was cut off, email it instead. To email it, send it to physiciansupport@cognizant.com from the email address on our TriZetto account. Fill in the brackets first: our practice name, Site ID, Tax ID and group NPI, and your own name and phone number. (I've already filled in the fee limits and where an order form goes.)

2. When TriZetto has answered every point, send me their written answer and fill in the short form at the bottom.
   - If TriZetto points you to a page in the portal for an answer (for example the Remittance Advice Settings or the ERA enrollment status), open it, change nothing, and copy what it shows into your reply to me.
   - If TriZetto asks you to call, or to contact another team, reply in the case asking them to pass it on and answer there in writing. If they won't, list it under "Still open".
   - If something is still open 7 days after you sent the request, send me what you have, with the case number.

Rules for anything TriZetto says or asks (they protect ModMed, which gets our claims and remittances through the same TriZetto account):
- Don't change anything in the TriZetto portal, even if TriZetto suggests it: not our existing SFTP password, not the Remittance Advice Settings, not file formats. One exception: you may set the NEW login's password yourself, but only after TriZetto has confirmed in writing that the page changes only the new login, and only if the page shows the new login's username, not our Site ID. Make it 16 or more letters and numbers (no symbols). If the page shows our Site ID, or doesn't say which login it changes, don't touch it: write that on the form. If TriZetto asks you to change anything else, don't: write it on the form.
- Never reset or change the password of our existing SFTP login. ModMed may sign in with it, and a new password would stop our claims and remittances.
- Don't download, re-download or re-send any remittance yourself, in the portal or anywhere else (for example "Show Archived" then "Download Selected"), even if TriZetto suggests it. That can send them to ModMed a second time, and RemitWatch collects them on its own. If that's TriZetto's answer, just write it on the form.
- Passwords never go by email or text: give them to me by phone or in person.
- If a message from TriZetto contains a password, patient names or claim details, or has remittance files or reports attached, don't forward it: copy everything else from it into your reply to me, tell me any password by phone, and delete their message.

----- THE REQUEST TO TRIZETTO (copy everything between the lines) -----

Subject: Second SFTP login for remittances, Site ID [Site ID]

Hello,

We are [practice name], Site ID [Site ID], Tax ID [Tax ID], group NPI [group NPI]. We would like a second SFTP login for our own reporting system, which only reads copies of our files (ModMed stays our billing system and sends our claims), and a few answers. Please answer each numbered point in writing in this case, with no passwords, patient names or remittance files in it. If a point belongs to another team (enrollment, technical, your account manager), please pass it on from this case and have them answer here in writing. If you can't answer a point, please say so.

Two conditions for everything below. Nothing may change what ModMed receives or sends through our account today (claims, remittances, reports, settings). And please don't reset or change the password of our existing SFTP login, because ModMed may use it. If anything below would need either, please don't do it; tell us instead.

Fees: if everything below costs no more than $[OWNER: one-time limit] one-time and $[OWNER: monthly limit] a month in total, please go ahead without waiting for our reply. If it would cost more, don't set it up; tell us the amounts. If an order form must be signed, please send it to [OWNER: name] at [OWNER: email].

1. A second SFTP login, with its own username and password, that receives a copy of every 835 remittance our account receives. Our system only reads files: it never deletes, renames, moves or uploads anything.
   a. When will it be ready, and what does it cost (if anything)?
   b. The username, the host and port (we expect sftp.gatewayedi.com, port 22), the folder the 835 files will be in, and how the files are named.
   c. The password: please don't put it in an email or in this case. Please phone it to [your name] at [your phone number]. If only we can set it, please tell us which page to use, and confirm in writing that the page changes only the new login's password, not our Site ID's existing SFTP password.
   d. How often the password expires, and who gets the notice. An unattended system signs in with this login, using a password (not an SSH key): can its password be set not to expire?
   e. The SFTP server's SSH host-key fingerprints: one for each key type it has (for example ED25519, ECDSA, RSA), each with its type. They start with SHA256: and aren't secret, so please write them in your answer. If you don't have them, just say so.
   f. Is SFTP sign-in to our account limited to listed IP addresses today? Only if it is, please add our server, 3.128.37.47, and keep every address already listed. If it isn't limited today, please don't turn a limit on.
   g. How long do files stay in the folder? Are they removed after they are downloaded, or after a number of days?
   h. If the new login only gets 835 files when "Auto Generate Remittances" is on: is that set for each login, or for the whole account? What are our Remittance Advice Settings today (Auto Generate on or off, file format, file naming)? Please don't change them.
   i. If a second login on our Site ID isn't possible, please set up another separate login instead (for example a child Site ID), but only if all of this holds: it has its own username and password, not our Site ID's; it gets a copy of every 835 our account receives; and our current Site ID keeps sending our claims and getting every 835, report and setting exactly as today. If none of that is possible (a folder under our existing login, for example, would share ModMed's password), please don't set anything up; tell us what you can offer.

2. How does ModMed connect to our account today: with our Site ID's SFTP login, or with its own connection? Does anything delete or move our files after downloading them?

3. Copies of our 999 and 277CA claim acknowledgments and claim rejection reports in the new login's folder, as X12 files. Copies only: please don't change the reports our account gets today. Is that possible, and in which folder?

4. Missing remittances. Once the new login exists, please re-send to it every 835 our account received from December 1, 2025 until the new login started getting copies, whatever the payment date. Our system ignores files it already has. Please send them only to the new login, not where ModMed collects our files. If you can't limit it to the new login, please don't re-send anything, and don't ask us to re-send or download them from the portal ourselves (that sends them where ModMed collects); tell us what else you can do. In particular we are missing:
   - UnitedHealthcare, payer ID 87726, payment dates December 22, 2025 to January 11, 2026. Optum reported that some UnitedHealthcare 835s from December 2025 to January 9, 2026 went out without the payee NPI. Please search by our Tax ID and that payer ID for those dates, including ERAs that arrived without our NPI. Are ours held or unmatched at TriZetto? If you find any, please don't release or re-match them to our account, and don't ask the payer to send them again, because ModMed would then receive them: put copies only in the new login, or tell us what you found.
   - Anthem, payer ID ANTHM, payment dates September 7 to 20, and September 28 to October 4, 2026.
   How far back does your remittance archive go?

5. ERA enrollment. Please send our ERA (835) enrollment status for every payer: payer, payer ID, Tax ID, NPI, status and effective date. We have never received an 835 from VA Community Care Network (payer ID VACCN) or TriWest (payer ID TWVACCN). How do we enroll for those: with you, or with the payer? Please don't start an enrollment yet; tell us what it needs.

Thank you,
[your name]
[practice name]

----- END OF THE REQUEST -----

THE FORM: reply to me with this filled in, together with TriZetto's written answer (without any password)

Case number: ______________________
Sent on (date): __________   How: portal Contact Us / email
TriZetto answered every point on (date): __________   Still open: ______________________
Password for the new login: phoned to me on (date) ______ / I set it on the page TriZetto named, on (date) ______ / not yet: ______________________
(Don't write the password here.)
Fees quoted (what for, how much, set up or not): ______________________
Anything TriZetto changed on our account: none / ______________________
Anything TriZetto asked you to do that you didn't (changing a setting, downloading files, calling): ______________________
Anything TriZetto told you by phone and not in writing: ______________________

Thank you!
```

## After the answer comes back (for whoever runs the server)

1. `sudo bash /opt/remitwatch/deploy/aws/configure.sh`, TriZetto section: the username, the password (given by phone or in
   person), how many days TriZetto lets it last, the folder the 835 files are in, and every host-key fingerprint TriZetto
   gave (separated by commas). Enter keeps every other setting.
2. Open the server's `/setup` page and press "Test the TriZetto connection": the server and its key, the sign-in,
   the files there and one remittance read. The next hourly run reads the remittances.
3. If TriZetto could only offer the Site ID's own login (shared with ModMed), don't enter it until ModMed confirms that a
   second reader on that login is safe (nothing it does deletes or moves the files first) and that password changes will
   be made in both places on the same day. RemitWatch never retries a refused password within a day, so it cannot lock
   that login, and the data checks warn 20 days before the password expires.
4. If the 999 and 277CA copies arrive in their own folder, leave that folder out of `TRIZETTO_SFTP_DIRS` for now: RemitWatch
   reads 835s; reading claim acknowledgments and rejections from TriZetto is a later step.

Why the rules in it (vendor guides; TriZetto publishes no specification): the SFTP login is usually the 4-character Site
ID with one password for the account, set in the portal under My Account, Manage Products, Manage SFTP Password, and
changing it stops every system that signs in with it; TriZetto makes it expire (60 days to a year); repeated failed
sign-ins lock it for an hour; Auto Generate Remittances is one setting for the whole account; "Show Archived" and
"Download Selected" in the portal sends remittances to wherever the account's files are collected; TriZetto takes written
requests through the portal's Contact Us link and at physiciansupport@cognizant.com (its Contact Us page); 277CA delivery
is switched on by request; a held or re-sent remittance arrives like a new one. Sources: TriZetto's Contact Us page and
client website guide (TRI629, 2025); EZClaim, ChiroTouch, ChiroUp, Barti, IntakeQ, PSS, PracticeSuite, drchrono, SOAPware,
ReLiMed, AngelTrack and Konica Minolta guides; UnitedHealthcare's 835 companion guide and EFT/ERA resolution procedures;
the January 2026 Optum payer update (missing payee NPI); Optum Pay's FAQ.
