# TriZetto request: what to ask, and what to send back

For the practice's TriZetto administrator. Everything needed is on this page; the only things you bring are the
practice's own Site ID and Tax ID (TriZetto can look the Site ID up by the Tax ID). Work through it on one call, fill in
the form at the end, and send the form back in one reply.

**Why.** The practice's billing dashboard (RemitWatch: the practice's own software, on the practice's own secure server)
needs to read a copy of the payers' remittance files (835 files, also called ERAs) straight from TriZetto. It only reads:
it never deletes, moves or uploads anything. ModMed already receives the practice's remittances through TriZetto and must
keep receiving them exactly as it does today; nothing in this request may change that.

## The call: TriZetto Provider Solutions support, 1-800-556-2231

Have ready: the practice's name, Tax ID and TriZetto Site ID. Ask for a ticket number at the start.

### 1. A separate, read-only SFTP login for the practice's own system

Say: "We need a second SFTP login on our Site ID, or a separate folder, that receives a copy of every 835 remittance
file. Our system only reads files; it never deletes or moves anything. ModMed must keep receiving our remittances exactly
as it does now."

Then ask, and write each answer on the form:

1. How does ModMed collect our remittances today: from our Site ID's SFTP mailbox, or through its own connection? Does
   anything delete or move files after downloading them?
2. Can you set up the second login (or the copy folder)? Is there a fee?
3. The host name and port. (We expect sftp.gatewayedi.com, port 22.)
4. The username and password. Password login is what our system uses; no SSH key is needed. Ask them to give the
   password by phone, and write it down: it goes to Neil by phone or in person, never by email or text.
5. The exact folder where the 835 files will appear, and, if available, the folders for the 999, 277CA and claim
   rejection reports.
6. The SFTP server's SSH host-key fingerprint (it looks like SHA256:…). Ask them to read it out or put it in the ticket.
7. How often the SFTP password expires, and who gets the notice before it does. Ask that the notice go to you.
8. Do you restrict SFTP access by IP address? If yes, ask them to allow the practice's server: **3.128.37.47** (its fixed
   address; originbilling.com points to it).

**If they say remittances only reach SFTP when "Auto Generate Remittances" is turned on** (a setting in TriZetto's
Remittance Advice Settings), decide on the call as follows:

- They confirm that turning it on for the new login or folder does **not** change what ModMed receives: let them turn it
  on.
- It would change or stop what ModMed receives: do **not** turn it on. Ask whether they can copy each 835 into the
  separate folder instead. If they cannot, change nothing and write down what they said.
- Either way, ask them to read back the current settings (Auto Generate on or off, file format, file naming) for the form.

### 2. ERA enrollment

1. "Please send our ERA (835) enrollment status report for every payer: payer, payer ID, Tax ID, NPI, status and
   effective date."
2. "We have never received an 835 from VA Community Care Network, payer ID VACCN (Optum). Please start ERA enrollment
   for it." If they also see claims to TriWest (payer ID TWVACCN), ask the same for it. If forms or signatures are
   needed, note which ones and who must sign.

### 3. Missing remittances

"Please re-deliver the 835s for these payment dates, or tell us how to get them:"

- UnitedHealthcare, payer ID 87726, payment dates **December 22, 2025 to January 11, 2026**. Optum reported that some
  UnitedHealthcare 835s from December 2025 to January 9, 2026 went out without the payee NPI: ask whether ours are held
  or unmatched at TriZetto. If TriZetto never received them, ask TriZetto to request a re-transmission from
  UnitedHealthcare EDI Support as our trading partner.
- Anthem, payment dates **September 7 to 20** and **September 28 to October 4, 2026**.
- "How far back does your remittance archive go?"

### 4. Claim acknowledgments and rejections

1. "Can our 999 and 277CA claim acknowledgments and claim rejection reports be delivered to SFTP, as X12 277CA files
   rather than a text report? In which folder?"
2. "Is Claim Status Inquiry (276/277) part of our contract? Real-time, batch, or both?"

## Also today (not TriZetto): Optum Pay

Whoever has the practice's Optum Pay login: download UnitedHealthcare's 835 files for payment dates December 22, 2025 to
January 11, 2026. Optum Pay's free tier keeps 13 months, so these start disappearing around late January 2027. Upload
the files at https://originbilling.com/upload (drop them on the page; files RemitWatch already has are recognised and
not counted twice).

## The form: send back to Neil in one reply

Everything except the password, which goes to Neil by phone or in person.

```
TriZetto ticket number: ______________________

1. How ModMed gets our remittances: ____________________________________________
   Does anything delete or move files after download?  yes / no: ________________
2. Second login or copy folder set up:  yes / no     Fee quoted: ________________
3. SFTP host: ______________________   Port: ______
4. SFTP username: ______________________   (password: by phone or in person)
5. Folder with the 835 files: ______________________
   Folders for 999 / 277CA / rejection reports: ______________________
6. Host-key fingerprint (SHA256:...): ______________________
7. Password expires every: __________   Notice goes to: ______________________
8. IP restriction:  none  /  our server address was allowed on (date): __________
9. Auto Generate Remittances was: on / off   File format: ________   Naming: ________
   Changed on this call?  no  /  yes, because: ______________________________
10. ERA enrollment report attached:  yes / no
    VACCN enrollment started on (date): __________   Forms or signatures needed: ________
    TriWest (TWVACCN): ______________________
11. Missing remittances. UnitedHealthcare: ______________________
    Anthem: ______________________   Archive goes back to: __________
12. 999 / 277CA to SFTP:  yes / no   Folder: ______________________
    Claim Status Inquiry in contract:  yes / no   Real-time / batch: __________
13. Optum Pay files for Dec 22, 2025 to Jan 11, 2026 uploaded:  yes / no
```

## After the form comes back (for whoever runs the server)

1. `sudo bash /opt/remitwatch/deploy/aws/configure.sh` and, in its TriZetto section, enter the username, the password,
   the 835 folder (item 5) and the host-key fingerprint (item 6). Enter keeps every other setting.
2. Open `https://originbilling.com/setup` and press "Test the TriZetto connection": the server and its key, the sign-in,
   the files there and one remittance read.
3. If TriZetto could only offer the Site ID's own login (item 2 is "no"), ask ModMed whether a second reader on that
   mailbox is safe before entering it.

Sources (vendor guides; TriZetto publishes no specification of its own): EZClaim's TriZetto guides, ChiroTouch and
ChiroUp help pages, Barti, IntakeQ, AngelTrack, Konica Minolta's clearinghouse sheet, SOAPware and ReLiMed (Auto Generate
Remittances), Stedi (one clearinghouse per payer for ERAs), UnitedHealthcare's 835 companion guide and EFT/ERA resolution
procedures, the January 2026 Optum payer update (missing payee NPI), Optum Pay's FAQ, TriWest/PGBA and VA community care
pages.
