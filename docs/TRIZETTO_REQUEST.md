# What to ask TriZetto (for the practice's admin)

RemitWatch can read the payers' remittances (835 files) straight from TriZetto instead of waiting for them to be copied
into Dropbox. It only reads: it never deletes, moves or uploads anything there. Everything below comes from public
clearinghouse guides; TriZetto publishes no specification of its own, so each point is asked rather than assumed.

The one risk to avoid: ModMed already receives the practice's remittances from TriZetto. Changing TriZetto's remittance
settings, or giving RemitWatch the same SFTP login ModMed uses, could change what ModMed's posting receives (a remittance
is put in an automatic file once; some integrations delete files after downloading; the SFTP password expires and a reset
breaks whoever was not told). So: ask, change nothing yourself, and ask for a separate login.

## The message (call TriZetto Provider Solutions support, 1-800-556-2231, or open a ticket)

> Practice: ______ · Tax ID: ______ · TriZetto Site ID: ______ (TriZetto can look it up by Tax ID)
>
> 1. **SFTP access for a second, read-only reader.** We run a remittance-monitoring system on our own server that only
>    reads our 835 files. Please:
>    - tell us how ModMed collects our remittances and claim reports today: from our Site ID's SFTP mailbox, or through a
>      ModMed-level connection? Does anything delete or move files after downloading them?
>    - issue a separate SFTP login (or a copy folder) that receives a duplicate of every 835 file, without changing how
>      ModMed receives them. Is there a fee?
>    - send us: the host and port (we have sftp.gatewayedi.com, port 22), the user name, the password (by phone or secure
>      email), the folder the 835 files land in (and the folders for 999, 277CA and claim reports), how often the
>      password expires and who gets the reset notice, and the SFTP server's SSH host-key fingerprint (SHA256).
>    - if you restrict SFTP by source IP address, allow our server's address: ______ (the AWS fixed address).
>    - read back our Remittance Advice Settings (is Auto Generate Remittances on, which file format and naming). Please
>      do not change them until we have confirmed with ModMed.
> 2. **ERA enrollment.** Send our ERA enrollment status report (payer, payer ID, TIN, NPI, status, effective date). We have
>    never received an 835 from VA Community Care Network (payer ID VACCN, Optum): please start ERA enrollment for it,
>    and for TriWest (TWVACCN) if we bill it.
> 3. **Missing remittances.** Please re-deliver (or tell us how to get) the 835s for:
>    - UnitedHealthcare, payer ID 87726, payment dates December 22, 2025 to January 11, 2026. Optum reported that some
>      UnitedHealthcare 835s from December 2025 to January 9, 2026 were sent without the payee NPI: are ours held or
>      unmatched? If you never received them, please request a re-transmission from UHC EDI Support as our trading
>      partner.
>    - Anthem, payment dates September 7 to 20 and September 28 to October 4, 2026.
>    - How far back does your remittance archive go?
> 4. **Claim acknowledgments and rejections.** Can our 999, 277CA claim acknowledgments and claim rejection reports be
>    delivered to SFTP (X12 277CA rather than a text report), and in which folder? Is Claim Status Inquiry (276/277) part
>    of our contract, real-time or batch?

## What the practice does itself, now

- **Download the UnitedHealthcare 835s for December 22, 2025 to January 11, 2026 from Optum Pay.** Its free tier keeps
  13 months, so the oldest of them drop out around late January 2027. Drop the files on the Upload page.
- Optional, for Anthem's missing weeks: Availity Client Services, 1-800-282-4548 (Anthem's EDI gateway), or Availity's
  remittance viewer.

## When TriZetto answers (Kevin, on the server)

1. `sudo bash /opt/remitwatch/deploy/aws/configure.sh`: the TriZetto section takes the user, the password, the folder the
   835 files are in, and the key fingerprint (Enter keeps every other setting).
2. Open `https://<the site>/setup` and press "Test the TriZetto connection": the server and its key, the sign-in, the files
   there and one remittance read.
3. If TriZetto could only offer the Site ID's own login, ask ModMed first whether a second reader on that mailbox is safe.

Sources (vendor guides; TriZetto's own pages were not reachable from the build environment): EZClaim's TriZetto guides,
ChiroTouch and ChiroUp help pages, Barti, IntakeQ, AngelTrack, Konica Minolta's clearinghouse sheet, SOAPware and ReLiMed
(Auto Generate Remittances), Stedi (one clearinghouse per payer for ERAs), UnitedHealthcare's 835 companion guide and
EFT/ERA resolution procedures, the January 2026 Optum payer update (missing payee NPI), Optum Pay's FAQ, TriWest/PGBA and
VA community care pages.
