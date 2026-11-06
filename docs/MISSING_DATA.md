# Data the practice has to supply, and why

Ranked by how much it changes what the system can prove.

1. **Annual ModMed EMA exports for 2024, 2025 and 2026** (Encounter and Charge Lines; ERA and Payment Line Adjustments; Claim and Claim Status). The prior analysis used files named `ODA - 2024.xlsx`, `Encounter and Charge Lines - 2025.xlsx` and so on, but they are not in the Dropbox folder; only weekly exports from July 27, 2026 onward are. With them, every finding shows how EMA handled the shortfall (WRITE_OFF, TRANSFER_RESPONSIBILITY, IGNORE), which is the direct evidence that billers adjusted money off instead of appealing. Who: whoever runs EMA Analytics. How: export the three reports per calendar year and drop them in `Trizetto Data/Reports/`.

2. **837 claim files on an ongoing basis.** TriZetto keeps the submitted 837P files; only October 5 to 9, 2026 are in Dropbox (`PROFESSIONAL_Claims_*.txt`). Routing them to the same folder every week lets the system compare each paid line with the exact line submitted (code, modifiers, units, charge) instead of inferring from the charge amount. Who: TriZetto account administrator (claim file retrieval or SFTP).

3. **Executed payer contracts and fee schedules with effective dates** (Anthem/BCBS Kentucky, Humana, UnitedHealthcare, Aetna, Cigna, UMWA, the larger TPAs). Turns "below the payer's usual rate" into "below contract" with the contract clause attached, and settles whether Humana MA at 95 percent of Medicare and Anthem commercial E/M at 59 to 72 percent are what was signed. Who: practice administrator.

4. **Payment vendor enrollment status** (Zelis, Optum Pay, Payspan, ECHO, InstaMed, Change Healthcare) for each payer: which are on virtual card, fee-bearing ACH, or free standard EFT. The FEE_SKIM findings quantify the fees; the enrollment records show what to switch.

5. **Provider roster** (NPI, credential MD/DO/PA/NP, start and end dates, supervising physician). Replaces the inferred physician/non-physician tiers and allows incident-to logic.

6. **MIPS feedback reports (QPP)** for performance years 2022, 2023 and 2024. The Medicare bonus fell from about $308K (2024 payment year) to $82K (2025) to $14K (2026); the reports say which category dropped.

7. **Prior authorization logs** for radiation therapy (G6001, 77xxx) and biologic injections (J3245 and similar): CO-197 denials are frequent and preventable.

8. **Denial and appeal work logs** from the billing team (what was appealed, when, outcome). Lets the system report overturn rates by payer and signal, and learn which appeals are worth the effort.

9. **Medicare fee schedule files** (CMS RVU files and the Kentucky and Indiana GPCIs for 2024 to 2026). Today the Medicare baseline is inferred from Medicare remits, which works for codes Medicare pays often; the official files extend it to every code and let Medicare Advantage underpayment be stated against the published amount.
