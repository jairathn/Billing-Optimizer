# CHANNELS research: medicaid family (notes)

Read on 2026-11-23. Public pages and PDFs only, no logins. Sources saved under sources/<name>/ (raw PDF or HTML plus
text: doc.txt, page.txt, or page.nd.txt for script-rendered pages). Fetch log with UTC times, HTTP codes and hashes:
sources/fetchlog.txt. Search terms used: payer names, product names, states only. Earlier lever notes used as hints only.

## Payer groups (payers.json, as assigned)
- Medicaid KY: 835 "KY MEDICAID", payer id 00661, trace 1610600439, phone 800-807-1232 (KY Medicaid FFS, fiscal agent Gainwell).
- Medicaid IN: 835 "IN MEDICAID", payer id 00711, phone 800-457-4584 (IHCP FFS, fiscal agent Gainwell).
- Humana Medicaid: 835 "HUMANA", payer ids 61101 and HUMANA, state KY (Humana Healthy Horizons in Kentucky; Indiana PathWays
  also covered because Humana's Indiana Medicaid plan is a Humana Medicaid product).
- WellCare / Ambetter (Centene) Medicaid: 835 "WELLCARE OF KENTUCKY, INC." 14163 (FL) and "AMBETTER" 68069 (MO).
- Aetna Medicaid: 835 "AETNA BETTER HEALTH OF KENTUCKY" 128KY.
- Anthem / BCBS Medicaid: 835 "ANTHEM" 00660 (OH, Availity).
- Medicaid (other): no 835 identities. Covers Passport by Molina (KY), UnitedHealthcare Community Plan of Kentucky, and the
  Indiana MCEs not in another group (CareSource, MDwise, MHS, UHC Community Plan of Indiana).

## Kentucky state rules (official KAR text, apps.legislature.ky.gov)
- 907 KAR 17:035 (Cert eff. 2-5-2025) Sec 2(1): EIR request to the MCO within 60 calendar days of receiving the MCO's final
  internal appeal decision; clock starts on electronic receipt / fax transmission date / postmark (+3 days by mail); request
  "shall be sent to the MCO: 1. Electronically; 2. By fax; or 3. By postal mail."
- 907 KAR 17:035 Sec 4 and 907 KAR 17:040 (Cert eff. 4-17-2024): administrative hearing request to "the department" within
  30 calendar days of DMS's written notice; electronically, by fax or by postal mail; hearing in Frankfort; $600 fee paid by the
  party that loses. The KAR prints no address or number.
- 907 KAR 17:010 (eff. 3-10-2022) Sec 4(3): provider may be an enrollee's authorized representative only with the enrollee's
  written consent for that specific appeal or hearing. Sec 5: enrollee state fair hearing only after the MCO internal appeal,
  written request to the department, postmarked or filed within 120 calendar days of the MCO's appeal decision letter.

## Kentucky universal forms (state forms hosted by MCOs; the chfs.ky.gov copies moved)
- chfs.ky.gov/agencies/dms/MAPForms/ProviderAppealRequestForm.pdf (linked from Humana's live G&A page) returns 404 today.
  chfs.ky.gov MCO Dispute Form (Rev 09.24.2025) and MCOAppealProcess.pdf (search results) also 404 today; DMS pages load fine.
- Anthem-hosted "Kentucky Medicaid MCO Provider Appeal Request" (Provider Appeal-MCO 042018, AKYPEC-1693-18 May 2018) lists
  OLD per-MCO fax numbers (Humana 1-855-262-9793; Passport 502-585-8461; Anthem 502-212-7336; Aetna 1-855-454-5585;
  WellCare 1-866-201-0657) and "within 30 days". OUTDATED: do not use its numbers; each MCO's current page controls.
- Humana-hosted "Kentucky Medicaid MCO Provider Grievance Form" (Provider Grievance-MCO 122020) lists grievance faxes:
  Anthem 1-855-384-4872; Aetna 1-855-454-5585; Humana 1-800-949-2961; Passport 1-866-315-2572; WellCare 1-866-388-1769;
  UHC Community Plan 1-801-994-1082. Grievances, not appeals; 2020; hints only.

## Humana Healthy Horizons in Kentucky
- Live page https://provider.humana.com/medicaid/kentucky-medicaid/grievances-and-appeals (Next.js; text in page.nd.txt):
  appeals within 60 calendar days of notice or action, 120 calendar days for a claim reduction or denial; mail Humana Healthy
  Horizons Grievance and Appeal Department, P.O. Box 14546, Lexington, KY 40512-4546; phone 800-444-9137; fax 800-949-2961;
  Availity; form: Kentucky Medicaid MCO Provider Appeal Request Form (link dead). Contracted rate disputes: 24 months; same box
  ("Contracted Rate Disputes"); fax 800-949-2961; Availity. EIR: 60 calendar days from receipt of the appeal resolution; mail
  P.O. Box 14546; fax 502-508-1796; email HumanaMedicaid_EIR_MFH@humana.com; EIR Request Form required.
- 2025 KY Provider Manual (PDF created 2026-01-26; the documents page still lists "Provider manual 2025" as current):
  ch.7 internal appeals "60 calendar days from the date of the original claim submission adjudication" (CONFLICT with page's
  60 days from notice / 120 days for claim reduction or denial); decisions in 30 calendar days (+14). Phone book: Provider
  Grievance and Appeals P.O. Box 14546, Fax 800-949-2961 (second source); contracted rate disputes fax 800-949-2961.
  Corrected claims: 365 days from DOS; paper marked "corrected" mailed to Humana Claims, P.O. Box 14601, Lexington, KY
  40512-4601; electronic CLM05-3 = 7. Admin hearing requests: CHFS DMS Division of Program Quality and Outcomes, 275 East Main
  St., 6C-C, Frankfort, KY 40621-0001; manual says fee "not to exceed $1,000" (KAR says $600: KAR controls).
  State fair hearings (members): DMS Division of Program Quality and Outcomes 275 E. Main St. 6C-C Frankfort KY 40621;
  phone 800-635-2570; Fax 502-564-0223; Email DMS.Hearings@ky.gov.
- EIR Request Form (PDF created 2024-08-27, modified 2025-03-18) and network notice (Oct 1, 2024): mail P.O. Box 14546, fax
  502-508-1796, email HumanaMedicaid_EIR_MFH@humana.com (second source for EIR fax). Form required; verbal or other written
  requests not considered; not eligible: contract reimbursement disputes, Medicare claims, SIU cases.
- Contracted Rate Dispute Form and Process (0825): fax 800-949-2961 (form prints "800-9492961", same digits); must attach the
  EOP and the misapplied contract provision; incomplete submissions rejected; decision letter within 30 calendar days.
- Records Humana asked for: national Humana PPI page "Medical Records Management" (live) and national claim-payment inquiry
  resolution guide 642103ALL0125 (PDF 2025-07-14): Availity MRM tool; mail Humana Medical Records Management, P.O. Box 14465,
  Lexington, KY 40512-4465; fax 866-305-6655; attach Humana's request letter. Not product-specific (covers all Humana lines).
- CONFLICT: the national guide prints 800-949-2961 as "Medicare expedited fax" (nonparticipating Medicare appeals) and says
  "For Medicaid specific claims, refer to this chart for the appropriate state". The Kentucky Medicaid publications (page,
  manual, forms) all print 800-949-2961 for Kentucky Medicaid provider appeals, grievances and rate disputes; for this product
  and state those control. Same fax line appears shared by Humana's grievance and appeal intake.
- Escalation (optional, not a regulated level): Claim Escalation Form (May 2025) to humanaproviderservices@humana.com;
  form lists plan type "Medicaid".

## Humana Healthy Horizons in Indiana (PathWays; Arcadian Health Plan)
- Live page https://provider.humana.com/medicaid/indiana-medicaid/claims-payments: informal dispute via Availity, mail
  (Humana Healthy Horizons in Indiana, Attn: Informal Dispute, PO Box 14169, Lexington, KY 40512-4169) or email
  INMedicaidClaimsResearch@humana.com, within 60 calendar days of the notice; formal dispute by mail (Attn: Formal Dispute,
  201 North Illinois Street, Suite 1200, Indianapolis, IN 46204) or email IndianaFormalDispute@Humana.com, within 60 calendar
  days after the 30-day informal step. Paper claims: Humana Claims, P.O. Box 14169. NO FAX for claim disputes.
- 2026 Provider Policy and Procedures Manual (880004IN1125; PDF created 2026-07-24): "Provider claim appeals can be submitted
  via mail or email using the formal claim dispute contact information." Informal: 60 days from notice, or 90 days from
  submission if Humana fails to decide in 30. Formal: decision in 45 calendar days. Binding arbitration (AHLA, Indiana Uniform
  Arbitration Act) mailed to Attn: Arbitration Notice, 201 N. Illinois St., Suite 1200. Corrected claims: denied ones are new
  claims (90 days from DOS); paid/partly paid ones "within 60 calendar days of the date of service or discharge" (wording looks
  wrong; Humana's 2024 IHCP Works deck said 60 days of the final outcome: flag). G&A department fax 800-949-2961 is for
  standard (member/UM) appeals, P.O. Box 14169 (ch.13) or P.O. Box 14163 (ch.11): not for provider claim disputes.
- Flyer 287002IN0823 (PDF modified 2025-03-21) and guide 567406IN0924 (PDF 2025-01-08) agree: informal by Availity/mail/email,
  formal by email/mail; records requested by PPI: Availity MRM, mail P.O. Box 14465, fax 866-305-6655 (second source for that
  fax, Indiana Medicaid-specific).
- State fair hearing (members; provider with member's written consent): OALP - FSSA Hearings.

## Indiana state level
- OALP pages (in.gov/oalp; live): FSSA Hearings, 100 N. Senate Avenue, Suite N802, Indianapolis, IN 46204; fax 317-232-4412;
  email fssa.appeals@oalp.in.gov; phone 317-234-3488 or 866-259-3573. Member appeals of HIP/HHW/HCC health care decisions go
  to the health plan first. Humana's 2026 IN manual ch.13 prints 317-232-4412 as a phone (error; OALP and Humana ch.11 say fax).
- IHCP FFS (Gainwell): Claim Administrative Review and Appeals module v7.1 (published 2026-03-24); IHCP page (live); BT202642
  (2026-03-24); BT2026175 (2026-10-22). Administrative review within 60 days of the RA: IHCP Portal secure correspondence
  (category Claim Administrative Review Request), the IHCP Claim Administrative Review Request form, or a letterhead letter,
  mailed to Gainwell Written Correspondence, PO Box 50442, Indianapolis, IN 46250-0418. Answer within 45 days. Appeal within
  15 calendar days of the review decision (plus statement of issues within 45 days): IHCP Portal secure correspondence (Appeal
  category) or mail MS07 Claim Appeals, IFSSA OMPP, 402 W. Washington St., Room W374, Indianapolis, IN 46204-2739.
  BT202642: "Please do not fax, email or mail the OALP directly." The module still prints OALP's fax 317-232-4412 and email
  under the OMPP address with a note pointing to BT202642: CONFLICT, bulletin controls. NO FAX for IHCP FFS reviews or appeals.
  (The lever hint "appeals to OMPP fax 317-232-4412" is wrong.)
- IHCP adjustments (module v8.0, 2026-01-29): portal Edit/Void or 837 replacement; paper "IHCP Professional, Dental, or
  Medicare Part B Crossover Claim Adjustment Request" to Gainwell - Adjustments, PO Box 50444, Indianapolis, IN 46250-0420;
  within 60 days of the RA. Attachments (Claim Submission and Processing module v8.5, 2026-02-24): portal upload, or mail with
  the IHCP Claims Attachment Cover Sheet to Gainwell - Claim Attachments, PO Box 50440, Indianapolis, IN 46250-0440 within 45
  days ("The IHCP accepts paper attachments only by mail"). Quick Reference Guide v14.1 (May 2026) lists the same boxes.

## Kentucky Medicaid fee-for-service (DMS; Gainwell)
- DMS physicians page (live): "Appeal requests for denied FFS claims must be submitted to Gainwell Technologies. The request
  must include the Provider Inquiry Form, reason for the appeal, and a hard copy claim." Timely filing 12 months from DOS.
- Provider Inquiry Form (PDF 2025-10-30): Gainwell Technologies, P.O. Box 2100, Frankfort, KY 40602. Physician billing
  instructions PT 64/65 v9.9 (Aug 13, 2026): Provider Inquiry Form to Gainwell Technologies, Provider Services, P.O. Box 2100,
  Frankfort, KY 40602-2100; adjustments to Gainwell Technologies, P.O. Box 2108, Frankfort, KY 40602-2108, Attn: Financial
  Services (attach corrected claim and paid RA page). Adjustment and Void Request Form (DMS approved Dec 7, 2020): adjustments
  can be done in KYHealth-Net "in most cases". kymmis contact page: Claims Submission P.O. Box 2101 Frankfort KY 40602-2101.
- NO FAX published for FFS claim appeals or adjustments. 907 KAR 1:671 (Crt eff. 9-24-2026) Sec 8-9: dispute resolution
  meeting (30 days, to the branch manager who issued the determination) then administrative hearing (30 days, Office of the
  Commissioner, DMS, 275 East Main Street, 6th Floor, Frankfort, KY 40621-0002) only for listed issues (sanctions, overpayment
  repayment, withholding, terminations, discrimination): not routine claim denials.

## WellCare of Kentucky (Medicaid)
- KY Medicaid Provider Quick Reference Guide 2026 (State Approved 04062026; PDF 2026-04-14): claim payment appeals (timely
  filing, incidental, unlisted, non-covered) in writing within 24 months of the EOP: provider.wellcare.com or mail WellCare of
  Kentucky, Attn: Claim Payment Appeals, P.O. Box 31370, Tampa, FL 33631-3370. NO FAX for claim payment appeals.
  Clinical appeals (no auth, exceeding auth, insufficient documentation, late notification; codes DN001, DN004, DN038, DN039,
  VSTEX, DMNNE, HRM16, KYREC) within 60 calendar days: mail Attn: Appeals Department, 13551 Triton Park Blvd., Suite 1200,
  Louisville, KY 40223, or fax 1-866-201-0657. Payment policy disputes 24 months: P.O. Box 31426 / CCR P.O. Box 31394 /
  P.O. Box 31416 by EOP code. Overpayment administrative review within 60 days: P.O. Box 31658 Tampa or fax 1-877-277-1805.
  EIR: 60 days; mail Attn: External Independent Third Party Review (Suite 1200), fax 1-800-509-8203, email
  Kyexternalreview@wellcare.com. Paper claims P.O. Box 31224 Tampa FL 33631-3224; faxed claim forms not accepted.
- Second sources: wellcare.com EIR bulletin (Last Updated On: 10/1/2026) fax 1-800-509-8203, email, mail Suite 1800
  (CONFLICT: suite 1200 vs 1800 for the same street address); admin hearing request to CHFS DMS Division of Program Quality
  and Outcomes, 275 East Main Street, 6C-C, Frankfort, KY 40621. WellCare-hosted state Provider Appeal Request form (MCO 012016)
  lists WellCare fax 1-866-201-0657 (old form; also old numbers for others).
- Corrected/voided claims (WellCare doc 05/2013): EDI CLM05-3 = 7 or 8 with REF*F8 original claim number; paper with
  frequency code and original claim number; subject to timely filing.

## Aetna Better Health of Kentucky
- Live page https://ch.aetnabetterhealth.com/kentucky/providers/grievance-appeal.html (www host returns 403 to scripts; the ch.
  host serves the same Aetna page): appeals "within 60 days of receiving a Notice of Action"; secure fax 1-855-454-5585
  ("fastest and best way"); phone 1-855-300-5528; mail Aetna Better Health of Kentucky, Attn: Complaint and Appeal Department,
  PO Box 81040, 5801 Postal Rd, Cleveland, OH 44181; email KYAppealandGrievance@aetna.com; Provider Portal (Availity).
  EIR: 60 calendar days; EIR Request Form; mail same box; fax 1-844-359-6670; email AetnaExternalReview@aetna.com.
- 2026 Provider manual (Aetna/2350 10-01-25 (1/26); PDF modified 2026-08-07): contacts "Complaints Appeals Address ... Attn: KY
  Appeals & Grievances PO Box 81040 ... Fax: 1-855-454-5585 email: KYAppealandGrievance@aetna.com" (second source); EIR fax
  1-844-359-6670 and AetnaExternalReview@aetna.com (second source). Window CONFLICT: manual "within one (1) year of the incident,
  remit date or date of notice of action" vs page 60 days (use 60). Paper claims (initial and corrected): P.O. Box 982969,
  El Paso, TX 79998-2969, CMS-1500 marked "corrected" or "resubmittal"; adjusted/corrected claims 24 months from first RA.
  Records requests: submission details are in each request letter; send within 30 days.

## Anthem Kentucky Medicaid (run-out)
- DMS contacts page (live): "As of Jan.1, 2025, Anthem is no longer a Medicaid Managed Care Organization, or MCO, in Kentucky."
  DMS letter Nov 12, 2024: Anthem processes claims up to 365 days from DOS and keeps the appeal process.
- Anthem notice (providernews, Nov 21, 2024): appeals "no later than 90 days from date of check or denial date" through Availity
  or mail to Anthem Claim Appeals, P.O. Box 62429, Virginia Beach, VA 23466-1599.
- KY Medicaid Provider Manual (KYBCBS-CD-PM-042746-23; PDF 2024-02-08): payment appeals verbal (855-661-2028), written (same
  box) or Availity; 90 calendar days of the remittance date; no fax for payment appeals. EIR: KYExternalReview@anthem.com, fax
  502-212-7336, mail Central Appeals Processing P.O. Box 62429 (23466-2429), Availity. EIR intake form (Oct 2024) repeats the fax
  and email (second source). Grievance fax 855-384-4872 (grievances only). The 2016/2018 universal appeal forms print
  502-212-7336 as Anthem's "appeal" fax: CONFLICT; Anthem's own manual uses it for EIR only.
- Member state fair hearing per Anthem manual: Office of the Ombudsman and Administrative Review, Attn: Medicaid Appeals and
  Reconsiderations, 275 East Main Street 2E-O, Frankfort KY 40621; phone 502-564-5497; fax 502-564-9523.

## Anthem Indiana Medicaid (HHW, HIP, HCC, PathWays)
- IN Medicaid Provider Manual (INBCBS-CD-PM-070775-24; PDF 2025-04-09): informal dispute verbally, Availity, or in writing with
  the Provider Dispute Resolution Request form to Provider Disputes and Appeals, P.O. Box 61599, Virginia Beach, VA 23466-1599,
  within 60 calendar days of the EOP; formal appeal Availity or writing within 60 calendar days of the reconsideration letter;
  decided in 45 days or "rendered in favor of the provider"; then binding arbitration per agreement. No fax for claim payment
  disputes (855-535-7445 is the grievance fax). Corrected claims and EOP record requests: Claim Follow-up Form to Corrected
  Claims and Correspondence, P.O. Box 61599 ("within 60 days of the date of service": wording as printed).

## MHS (Managed Health Services, Indiana Medicaid, Centene)
- Provider Manual 2025 (0126.PR.P.JB; PDF modified 2026-02-09): informal dispute via Secure Provider Portal or mail to P.O. Box
  3002, Farmington, MO 63640 within 60 calendar days of the EOP; administrative claim appeal (letterhead, "appeal") by mail
  within 60 days of the informal resolution; 45-day rule (overturned if late); pre-arbitration review by mail (429 N.
  Pennsylvania Street, Suite 109, Indianapolis, IN 46204) or fax 1 (833) 364-2498, postmarked within 60 days of the appeal
  decision. Newsroom item (09/04/24) repeats the fax (second source). No fax for disputes or appeals.
  Corrected claims within 60 days of the EOP (field 22 code 7 + original claim number; EDI CLM05-3=7 + REF*F8).

## CareSource Indiana (HHW, HIP)
- Live page (Updated 07/25/2025): disputes within 60 days of the written determination via Provider Portal or Claim Dispute
  Form; appeals within 60 days of the dispute resolution via portal or the Provider Clinical/Claim Appeal Form. Forms (OMPP
  approved 2023/2024): portal preferred; mail CareSource Grievance & Appeals Department, P.O. Box 2008, Dayton, OH 45401;
  corrected claims EDI or paper to Claims Department, P.O. Box 3607, Dayton, OH 45401-3607.
- Provider Manual (PDF 2025-04-23): disputes by portal, verbal, fax (937) 531-2398 or mail Claim Disputes Department P.O. Box
  2008; appeals portal, writing, mail Attn: Provider Appeals P.O. Box 2008, Fax: 937-531-2398; 45-day rule (approval if late).
  IHCP Works 2026 CareSource Dental deck (Sept 29, 2026): "Provider Claims Disputes Fax Number: 937-531-2398" and appeals fax
  1-937-531-2398 (second source, same digits). IHCP Works 2026 CareSource Medicaid claims deck lists portal and mail only.
- External review (member IRO, 120 days): P.O. Box 8738 Dayton, fax 844-417-6262 (page; manual fax table lists 844-417-6262
  under Provider Appeals).

## MDwise (exited HIP and HHW on Jan 1, 2026)
- mdwise.org (live): claims for DOS before 1/1/2026 to MDwise, PO Box 1575, Flint, MI 48501; provider mailbox
  ProviderServices@MDwise.org until March 31, 2027. Closure notice (Dec 2025): "Requests for administrative review and appeals
  related to claims adjudicated by MDwise will be the responsibility of MDwise." No current dispute fax or address published.

## UnitedHealthcare Community Plan of Kentucky
- 2026 Care Provider Manual (PMI 05202026; PDF 2026-06-15): "*We no longer use fax numbers." Reconsideration (24 months from
  PRA) and formal appeal (60 calendar days) through the UnitedHealthcare Provider Portal ("Most care providers in your state
  must submit ... electronically"); resubmissions P.O. Box 5270 Kingston NY. EITPR: mail Attn: Appeals and Grievances Unit
  External Reviews P.O. Box 31364 Salt Lake City; email eir_ky@uhc.com; form required. Admin hearing request (30 days) to
  Office of the Commissioner, DMS, 275 East Main Street, 6th Floor. Member fair hearing: Office of the Ombudsman and
  Administrative Review 2E-O, fax 1-502-564-9523, email providermcoinquiry@ky.gov.
- EIR Request Form (PCA-1-25-01082, May 2025) lists "fax to 844-800-8833": CONFLICT with the newer manual; not used.

## UnitedHealthcare Community Plan of Indiana
- Hoosier Care Connect Provider Manual (PDF 2025-06-30; Doc PMG-20231012): reconsiderations via portal or mail P.O. Box 5240,
  Kingston, NY 12402-5240; appeals via portal or mail Grievances and Appeals P.O. Box 31364 Salt Lake City UT 84131-0364;
  no fax.

## Passport by Molina (Kentucky)
- 2026 manual and forms on molinahealthcare.com / molinamarketplace.com return a not-found page or redirect to /ifp for
  scripted requests; a header-spoofing retry was refused and not attempted further. UNVERIFIED search excerpts: provider
  appeals fax (866) 315-2572; EIR fax (502) 585-8334, ReviewRequests@passporthealthplan.com. The 2020 state grievance form
  lists Passport grievance fax 1-866-315-2572; the 2016/2018 appeal forms list 502-585-8461. No fax recorded as a channel.
  DMS contacts page (live) lists Passport provider relations 800-578-0775.

## Ambetter (identity AMBETTER 68069 in this group; Centene marketplace, not Medicaid)
- Ambetter from WellCare of Kentucky Provider and Billing Manual 2026 (PDF 2026-07-21): payer ID 68069; reconsideration via
  Secure Provider Portal (preferred), phone, Claim Dispute form or letter mailed to Attn: Request for Reconsideration, P.O. Box
  5010, Farmington, MO 63640-5010; claim dispute/appeal on the form mailed to Attn: Claim Dispute, P.O. Box 5010; 180 days par
  / 90 days non-par from the EOP; corrected claims portal, EDI or Attn: Corrected Claims P.O. Box 5010. Fax 1-833-886-7956 is the
  Appeals and Grievances fax for medical necessity authorization appeals (with participant consent), not claim disputes.
  Indiana Ambetter (Ambetter from MHS) uses the same payer id: see the medicare_advantage family's Ambetter entry.

## Second pass (2026-11-23, after the first build): checks and corrections
- Every quote in research.json is checked by ../medicaid_scripts/build_json.py against the saved text (parts joined by " ... " are
  found in order; whitespace, curly quotes, dashes, ligatures, zero-width and PDF control characters normalized). Every fax value's
  digits appear in its own quote. Result: 16 entries, 121 channels, 17 fax channels, 62 windows.
- Ambetter from WellCare of Kentucky: downloaded the KY Claims, Disputes & Recovery/CCU Guide (internal approved 08/18/2026, PDF
  2026-10-24) and the KY Quick Reference Guide (January 2027). Claim payment disputes: secure provider portal or Ambetter, Attn: Claims
  Disputes/Appeals, P.O. Box 5010, Farmington, MO 63640-5010; par 180 / non-par 90 days. This settles the manual's three different
  dispute addresses (5010/-5010, 5010/-5000, Level II 5000/-5000). Appeals and Grievances fax 1-833-886-7956 is in both the 2026 manual
  (authorization appeals) and the QRG (two sources); recorded as first_level fax limited to authorization and medical necessity
  denials, not claim payment disputes.
- Ambetter Health of Indiana (same payer id 68069): downloaded the IN CCU Guide (Sept 2026), IN QRG (January 2027) and IN Appeals and
  Grievances Guide (Oct 2024). Payment disputes: portal or Attn: Claims Disputes/Appeals P.O. Box 5010, 180 days par and non-par.
  Medical reconsiderations (no auth, exceeded auth, insufficient documentation, late notification) by mail, email or fax
  1-833-886-7956 within 90 days (two sources: appeals guide and QRG). New entry added.
- The medicare_advantage family records 1-833-886-7956 as a general first-level Ambetter appeal fax and 1-866-201-0657 as the Wellcare
  MA appeal and payment dispute fax; for the Medicaid-group products the payer documents limit both to clinical/authorization appeals.
  Flagged in conflicts for the merge.
- CareSource IN: the 2026 IHCP Works medical claims deck adds a records channel ("If your claim was denied requesting medical records,
  those should be submitted via the CareSource Provider Portal") and lists only portal and mail for disputes and appeals. The manual's
  fax table lists both 844-417-6262 and 937-531-2398 under "Provider Appeals"; disputes and appeals sections name 937-531-2398. The
  dental deck's 937-531-2398 is for dental claim disputes and appeals (same P.O. Box 2008); kept as the second source with that caveat.
- Removed claims not backed by a saved source: Humana IN "2024 IHCP Works deck" remark, UHC IN "claims page excerpt" window, CareSource
  notice IN-MED-P-488869, Aetna "907 KAR 17:015" on the web page (it is in the manual), Anthem IN "page 2 of the form", Anthem KY run-out
  "listing of disputed claims", WellCare EIR "state intake form" (the WellCare bulletin names no form; the intake form was created by
  Humana with DMS per Humana's notice).
- Added: Humana IN Availity steps (manual: Claim Status > Dispute Claim > Appeals worklist); Anthem IN second corrected-claim window
  (60 days from the EOB or letter) as a conflict; UHC KY formal appeal window (60 calendar days from the PRA) and the UHC hearing
  quote (UHC cites 907 KAR 1:671); UHC IN reconsideration by portal, phone or mail with the Claim Reconsideration Request Form.
- DMS: the chfs.ky.gov "dpqo" page now shows a "Division of Quality and Population Health"; hearing addresses printed by plans differ.
  No hearing fax recorded.
