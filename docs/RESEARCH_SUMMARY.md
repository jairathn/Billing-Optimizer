# Research summary: what was checked against a primary source, and what was not

The two long files next to this one (`RESEARCH_DOMAIN.md`, `RESEARCH_TECH.md`) are the raw notes of the research runs. Most entries in them are tagged UNVERIFIED because the runs were cut short before their checking stage. This page lists what the application relies on and how each item was confirmed. Anything not listed here should be treated as a lead, not a fact.

## Read in the primary source during the build (November 12, 2026)

| claim used by the application | source | where it is used |
|---|---|---|
| Anthem Kentucky commercial: a Claim Payment Reconsideration may be filed for "a minimum of two (2) years from the issue date of the EOP", unless state law or the provider agreement says otherwise; a Claim Payment Appeal within 90 days of the reconsideration decision; a reconsideration is required before an appeal | 2026 Anthem Blue Cross and Blue Shield Kentucky Commercial Provider Manual, effective September 1, 2026, pages 58 to 60. https://www.anthem.com/content/dam/digital/docs/provider/commercial/manuals/PM_KY_00011.pdf | filing dates on Anthem commercial findings; letter wording |
| Anthem states it may review E&M claims before payment to decide whether the level submitted is higher than the level "supported on the Claim", and may deny, pend or reduce payment | same manual, page 71 | the argument in visit downcoding letters to Anthem commercial |
| Anthem's dispute must include a position statement, provider name, address, phone, email and NPI or TIN, the member's name and Anthem ID, the claim number and date of service, and supporting documents | same manual, page 60 | attachment checklist |
| Anthem Kentucky Medicaid: payment appeals must be received within 90 calendar days of the remittance date | Anthem Kentucky Medicaid Provider Manual, page 112. https://providers.anthem.com/docs/gpp/KY_CAID_ProviderManual.pdf | filing dates on Anthem Medicaid findings |
| KRS 304.17A-702: a clean claim must be paid, denied or contested within 30 calendar days | Kentucky Legislative Research Commission, statute id 29329 | letters |
| KRS 304.17A-708: an insurer shall not require a provider to appeal errors in payment where the claim was not paid according to the contracted rate; correction within 30 calendar days of documentation; no duty to correct after 24 months; retroactive denial limited to 24 months except for fraud | statute id 29333 | underpayment letters |
| KRS 304.17A-714: overpayment notice within 24 months with the covered person, date of service, claim reference and basis; 30 days to dispute; no recoupment while a timely dispute is open | statute id 29336 | takeback letters |
| KRS 304.17A-730: interest of 12, 18 and 21 percent a year on late clean claims, owed without a request | statute id 29344 | letters |
| KRS 304.99-123: fines for noncompliance with the prompt-payment statutes | statute id 47798 | background |
| Indiana Code 27-8-5.7-6.5: an insurer may not alter the CPT code on a clean claim or pay a code of lesser value unless the code breaks coding or clinical guidelines or the contract, or an employee or contractor of the insurer has reviewed the medical record | FindLaw, text current as of January 1, 2026. https://codes.findlaw.com/in/title-27-insurance/in-code-sect-27-8-5-7-6-5/ | downcoding letters, as "if the member's coverage is issued in Indiana" |

The Kentucky statutes are at `https://apps.legislature.ky.gov/law/statutes/statute.aspx?id=<id>`. They apply to insurers regulated by Kentucky. They do not reach Medicare, Medicare Advantage, federal employee plans or self-funded employer plans, which is why the letters say "to the extent this coverage is subject to Kentucky insurance law". A remittance does not say whether a plan is self-funded.

## Payer windows read in the primary source (November 26, 2026)

The A17 research read the filing, corrected-claim and appeal windows of the practice's payer groups in the regulations, the
statutes and the payers' 2026 manuals, and found 28 places where `remitwatch/codes.py` disagreed (the review of November 26,
section "Deadlines", with its answers-8). Each window below was then read again in the downloaded source before the code
changed; its words are in the rule's label, which every item's deadline basis repeats. An item has two dates: the safe date
the biller works to, and the payer's own last day; it expires only after the last day. A window the product had too long now
has the earlier date in both; one it had too short keeps a safe date no later than the true one and gets the true last day.

| window | the source's words | source | what the application does |
|---|---|---|---|
| Medicare Part B first filing | "no later than the close of the period ending 1 calendar year after the date of service"; a last day on "a Saturday, Sunday, legal holiday" is "extended to the next succeeding workday" | 42 CFR 424.44(a)(1) and (c), eCFR as of 2026-11-24 | the safe date is the calendar year; the last day moves past weekends and Federal holidays (5 U.S.C. 6103, observed days included) |
| Medicare Advantage, a provider not contracted with the plan | a reconsideration "within 60 calendar days after receipt" of the notice, receipt presumed 5 days after its date; Anthem: "within 65 calendar days from the remittance notification" | 42 CFR 422.582(b); Anthem BCBS Medicare Advantage Provider Guidebook (January 1, 2027 edition), 7.2 | named in the Humana, UnitedHealthcare and Anthem Medicare Advantage labels; the dates stay the contracted ones, because the practice bills these plans as a contracted provider |
| UnitedHealthcare corrected claim | the agreement's filing limit from the date of service: "This number of days also applies to submission of corrected claims" (the guide's example: 90 days) | 2026 UHC Administrative Guide, chapter 10, Timely filing of corrected claims | a corrected claim is dated 90 days from the service; the 12-month reconsideration stays open after it as the payer's last day |
| Wellcare Medicare Advantage first filing | "Timely Filing guidelines: 180 days from date of service." | Wellcare Kentucky Medicare Quick Reference Guide, January 2026 | 180 days (before: 12 months) |
| Federal Employee Program and GEHA first filing | "by December 31 of the following year after you received the service"; GEHA: "by December 31 of the year after the year you received the service" | 2026 brochures RI 71-005 and RI 71-006, Section 7 | December 31 of the year after the service (before: 90 days for the FEP, 12 months for GEHA); a returned FEP claim the same |
| GEHA reconsideration | "Write to us within 6 months from the date of our decision" | RI 71-006, Section 8; 5 CFR 890.105(b)(1) | 180 days as the safe date, 6 months the last day |
| Aetna reconsideration | "File your reconsideration within 180 calendar days of the initial claim decision"; fully insured plans: 2 years in Kentucky (participating providers) and Indiana | Aetna disputes and appeals overview and its state exceptions page, read 2026-11-26 | 180 days, now confirmed; the 835 does not say whether a plan is fully insured, so the shorter window stays and the label says so |
| Ambetter first filing | initial claims: participating 180 calendar days, non-participating 90, from the date of service | Ambetter Provider and Billing Manuals 2026, Kentucky and Indiana, Timely Filing | 90 days as the safe date, 180 the last day |
| Anthem Medicaid, Indiana plans | "Participating providers have 90 calendar days from the date of service"; a dispute "within 60 calendar days from the date on the EOB" | Anthem (Indiana Medicaid), IHCP Works 2026: Top denials and quick tips (October 2026) | told apart by the plan name on the 835; a plan name that names no state gets these shorter windows, and its label says so |
| Anthem Medicaid, Kentucky plans | claims "within 365 calendar days of the date of discharge for inpatient services and date of service for outpatient services"; a corrected claim "within the applicable timely filing requirements of the original claim"; a payment appeal within 90 calendar days of the remittance | Anthem Kentucky Medicaid Provider Manual; reimbursement policy G-16001 | 365 days to file and to correct; 90 days to appeal, and since April 14, 2026 the statute's 120 days below as the last day |
| Kentucky Medicaid managed care appeals | "within one hundred twenty (120) days of confirmed receipt of a notification from the managed care organization that payment for a submitted claim has been reduced or denied" | KRS 205.534(1)(d)1, effective April 14, 2026 (2026 Ky. Acts ch. 179) | by the date of the denial: a remittance dated on or after April 14, 2026 gets 120 days from receipt, presumed 5 days after the remittance (125 days); the plan's own shorter window stays the safe date |
| WellCare of Kentucky Medicaid | claims, "corrected and voided", "within 12 months" of the service (or 6 months from a primary payer's payment, if later); "Claim payment appeals must be submitted to WellCare in writing within 24 months of the date of denial of the EOP"; other appeals within 60 days | WellCare of Kentucky 2025-2026 Kentucky Medicaid Provider Manual | 12 months to file and to correct; 24 months for a coding, bundling or coverage denial; 60 days (125 by the statute) for a utilization-management denial or one whose reason is not known |
| Kentucky Medicaid fee-for-service | no appeal of an ordinary claim denial: the administrative appeal is for terminations, sanctions, overpayments to repay and withheld payments; claims "within twelve (12) months of the date the service was rendered"; a claim billed to Medicare first "Within six (6) months of the Medicare adjudication date" | 907 KAR 1:671 Sections 8 and 9; 907 KAR 1:673 Section 2(1)(b); 907 KAR 3:005 Section 1 | a denial is worked by a corrected or new claim within 12 months of the service: its date, its next step and its words say so |
| Indiana Medicaid fee-for-service | claims "must be originally filed within 180 days of the date of service"; administrative review "within 60 calendar days of notification of claim payment or denial"; a corrected denied claim is a new claim (180 days); a paid claim's adjustment "within 60 days of notification of the claim's disposition" | IHCP modules Claim Administrative Review and Appeals (March 24, 2026) and Claim Submission and Processing (November 24, 2026), Section 11 | 180 days to file (before: 365), 60 days to dispute (before: 90), corrected claims by whether the line was paid |
| TRICARE returned claim | "no later than the later of" one year after the services or 90 days from the return | 32 CFR 199.7(d)(1) | a corrected claim dated a year from the service |
| CHAMPVA | claims "One year after the date of service"; a reconsideration "within one year of the date of the initial determination" | 38 CFR 17.276(a)(1) and 17.277(b) | both, under every name CHAMPVA's remittances use (before: the unknown-payer 180 days, and the filing rule missed one name) |
| Oxford | a reconsideration and appeal "within 12 months (or as required by law or your Agreement) from the date of the original EOB or PRA"; its filing limit is the agreement's | 2026 UHC Administrative Guide, Oxford commercial supplement | 12 months (before: Medicare's 125 days, because its claims carry Medicare's filing indicator); the filing limit is the unknown payer's, unconfirmed |
| Kentucky workers' compensation | bills "following treatment within forty-five (45) days" | 803 KAR 25:096; KRS 342.020 | 45 days from the treatment (before: 12 months); Indiana's limit is not checked |

A payer the name rules do not know whose claims carry Medicare's filing indicator (MA, MB) is not Medicare: its windows are
no longer called confirmed, and its first filing is the unknown payer's.

## Confirmed in the practice's own remittances

- Reason code 186 ("level of care change adjustment") is the mark of Anthem's visit downcoding. On Anthem commercial it comes with the billed code reported in SVC06. On Anthem Medicare Advantage and Medicaid the billed code is left in place and the allowed amount is cut to the next level down, with no other sign.
- Downcoding is concentrated: two Anthem product lines carry nine in ten downcoded visits, one provider carries 44 percent of them, and the commonest change is a new-patient level 4 paid as level 3.
- Out-of-state Blue plans (remittance plan name "ITS HOST") account for a quarter of downcoded visits, so the home plan's edits follow the member to Kentucky.
- Anthem began changing radiation treatment codes in April 2026 (126 lines, about $43K), which is the largest code-change item in the archive.
- UnitedHealthcare Medicare Advantage cut its usual allowed amounts on common codes by 8 to 14 percent in February and March 2026.

## Reported in research notes, not re-read

| claim | status |
|---|---|
| Humana: disputes within 18 months of the paid date | research note |
| Cigna: payment review within 180 days | research note |
| Indiana Code 27-13-36.2-4.5 (the HMO counterpart of 27-8-5.7-6.5) | quoted by a research agent; the insurer section was re-read, this one was not |
| Medicare from January 1, 2027: when an office visit with modifier 25 is furnished on the same day as a 0-day global procedure, the most expensive service is paid at 100 percent and the other visits and 0-day procedures at 80 percent (10- and 90-day globals and the proposed 50 percent were not finalized). G2211 becomes modifier B1 | CMS, CY 2027 Physician Fee Schedule final rule fact sheet (November 4, 2026); Federal Register November 9, 2026 (CMS-1848-F). The engine expects the 80 percent for Medicare and Medicare Advantage lines from that date, in denial sizing and in the underpayment check, using CMS's own global periods (remitwatch/pfs.py) |
| Anthropic signs a BAA for HIPAA-ready API organisations; the Messages API is covered | Anthropic privacy centre article, read through a search summary |
| Hosting: AWS, Google Cloud and Azure sign a BAA at no charge; Fly.io and Render sell HIPAA plans; GitHub Actions has no BAA | research notes, pricing as of November 2026 |

## Open questions worth an hour of someone's time

1. The filing window in each executed participation agreement. It overrides the manual.
2. Whether Anthem's provider dispute period for federal employee plan claims is longer than the member's 6 months (5 CFR 890.105). The application uses 180 days as the safe date and 6 months as the last day.
3. The reason codes Medicare will use on remittances for the 2027 same-day visit rule, and whether it stacks with the multiple-procedure reduction (no CMS change request yet).
4. Which plans are self-funded, which decides whether the Kentucky statutes or ERISA governs a dispute.
5. Whether the practice is contracted with every Medicare Advantage plan it bills (the dates assume it is) and with Ambetter
   as a participating provider. A provider that is not contracted has 60 days from receipt for a Medicare Advantage
   reconsideration and 90 days for an Ambetter claim.
6. Humana's Medicaid plans in Kentucky and Indiana: the 835 does not say which, and the application uses Kentucky's 120 days.
