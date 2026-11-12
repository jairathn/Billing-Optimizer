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

## Confirmed in the practice's own remittances

- Reason code 186 ("level of care change adjustment") is the mark of Anthem's visit downcoding. On Anthem commercial it comes with the billed code reported in SVC06. On Anthem Medicare Advantage and Medicaid the billed code is left in place and the allowed amount is cut to the next level down, with no other sign.
- Downcoding is concentrated: two Anthem product lines carry nine in ten downcoded visits, one provider carries 44 percent of them, and the commonest change is a new-patient level 4 paid as level 3.
- Out-of-state Blue plans (remittance plan name "ITS HOST") account for a quarter of downcoded visits, so the home plan's edits follow the member to Kentucky.
- Anthem began changing radiation treatment codes in April 2026 (126 lines, about $43K), which is the largest code-change item in the archive.
- UnitedHealthcare Medicare Advantage cut its usual allowed amounts on common codes by 8 to 14 percent in February and March 2026.

## Reported in research notes, not re-read

| claim | status |
|---|---|
| UnitedHealthcare: reconsideration and appeal both within 12 months of the original EOB or PRA | quoted from the 2026 administrative guide by a research agent |
| Humana: disputes within 18 months of the paid date | research note |
| Aetna: reconsideration within 180 days, appeal within 60 days of that decision | research note |
| Cigna: payment review within 180 days | research note |
| Anthem Medicare Advantage: reconsideration within 120 days of the EOP, appeal within 30 days | research note quoting the provider guidebook |
| Indiana Code 27-13-36.2-4.5 (the HMO counterpart of 27-8-5.7-6.5) | quoted by a research agent; the insurer section was re-read, this one was not |
| Medicare from January 1, 2027: when a visit with modifier 25 is billed with a 0-day global procedure, the lower-paid of the two is paid at 80 percent | trade press reports of the CY 2027 fee schedule final rule (released November 2026). Not checked against the Federal Register. The detection engine already expects it for Medicare lines from that date so they are not flagged, and will flag other payers that copy it |
| Anthropic signs a BAA for HIPAA-ready API organisations; the Messages API is covered | Anthropic privacy centre article, read through a search summary |
| Hosting: AWS, Google Cloud and Azure sign a BAA at no charge; Fly.io and Render sell HIPAA plans; GitHub Actions has no BAA | research notes, pricing as of November 2026 |

## Open questions worth an hour of someone's time

1. The filing window in each executed participation agreement. It overrides the manual.
2. Whether Anthem's two-year window applies to federal employee plan claims processed by Anthem. The application uses 180 days for those to be safe.
3. The Federal Register text of the 2027 same-day visit rule and the reason codes Medicare will use for it.
4. Which plans are self-funded, which decides whether the Kentucky statutes or ERISA governs a dispute.
