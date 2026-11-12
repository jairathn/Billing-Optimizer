# Data the practice has to supply, and why

Ranked by how much it changes what the system can prove.

1. **A current remittance feed.** The newest payment in Dropbox is dated October 9, 2026. Whoever downloads the TriZetto remittance files and uploads them to `Trizetto Data` has stopped, or uploads in batches a month apart. The alerts can only be as current as the files. Who: billing lead.

2. **837 claim files on an ongoing basis.** TriZetto keeps the submitted 837P files; only a few days of them are in Dropbox (`PROFESSIONAL_Claims_*.txt`). They are the proof of what was billed. With them the system catches the case the billers described, where the remittance shows a level 3 billed and paid and only the claim shows it went out as a level 4. On the days that are present, the submitted code matched the paid code on every line, so that pattern has not appeared yet, but it can only be seen where claim files exist. Who: TriZetto account administrator (claim file retrieval to the same Dropbox folder).

3. **Annual ModMed EMA exports for 2024, 2025 and 2026** (Encounter and Charge Lines; ERA and Payment Line Adjustments; Claim and Claim Status). Only weekly exports from July 27, 2026 onward are in Dropbox. With the annual files every finding shows how EMA handled the shortfall (written off, moved to the patient, ignored), and the breakdowns by location and diagnosis cover the whole history. Today location is known for recent claims and estimated from each provider's usual site for the rest, and diagnosis is known only for recent claims. Who: practice administrator (EMA report export).

4. **Executed payer contracts and fee schedules with effective dates** (Anthem, Humana, UnitedHealthcare, Aetna, Cigna, UMWA, the larger TPAs). Turns "below the payer's usual rate" into "below contract" with the clause attached, settles the filing window for each payer, and settles whether Anthem commercial visits at 59 to 72 percent of Medicare and the multiple-procedure terms are what was signed. Who: practice administrator.

5. **Payment vendor enrollment status** (Zelis, Optum Pay, Payspan, ECHO, InstaMed) for each payer: virtual card, fee-bearing ACH, or free standard EFT. The fee findings quantify the fees; the enrollment records show what to switch.

6. **Provider roster** (NPI, credential, start and end dates, locations). Replaces the inferred physician and non-physician tiers and the inferred usual site, and cleans up the provider names, which today are taken from the payers' spellings.

7. **Appeal outcomes that never reach a remittance.** A won appeal shows up as a payment and is recorded automatically. A lost appeal does not, so the billers need to mark it "lost" in the dashboard. Without that the overturn rate by payer cannot be computed.

8. **MIPS feedback reports (QPP)** for performance years 2022 to 2024. The Medicare bonus fell from about $308K (2024 payment year) to $82K (2025) to $14K (2026); the reports say which category dropped.

9. **Prior authorization logs** for radiation therapy and biologic injections: authorization denials are frequent and preventable.

10. **Medicare fee schedule files** (CMS RVU files and the Kentucky and Indiana GPCIs for 2024 to 2027). Today the Medicare baseline is inferred from Medicare remittances, which works for codes Medicare pays often. The official files extend it to every code. They matter more in 2027, when Medicare is reported to start paying the lower of a same-day visit and minor procedure at 80 percent (see docs/RESEARCH_SUMMARY.md).
