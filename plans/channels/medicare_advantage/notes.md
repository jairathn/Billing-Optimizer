# CHANNELS research: medicare_advantage family (notes)

Read on 2026-11-23. Public pages only, no logins. Sources saved in sources/ (raw file plus .txt or .nd.txt text).
Search terms used: payer names, product names, states only.

## Payer groups (from payers.json, largest open dollars first)
- Humana Medicare Advantage: 835 payer ids HUMANA and 61101, state KY. The 835s point to two Humana documents:
  the "Information for Non Participating Providers" PDF (live: it is the Grievances and Appeals/Inquiry Directory) and
  apps.humana.com/marketing/documents.asp?file=2859948 (returned HTTP 503 today; not usable as proof).
- UnitedHealthcare Medicare Advantage: 835 payer id 87726 (MN, UT).
- Anthem / BCBS Medicare Advantage: 835 payer ids ANTHM, 00660, 00630 (OH; Availity).
- WellCare / Ambetter (Centene): no 835 identities.
- WellCare / Ambetter (Centene) Medicare Advantage: 835 payer id 68069, name AMBETTER, state MO.
- United Healthcare Dual Complete: no 835 identities (UHC's D-SNP brand).
- Humana Medicare Advantage Medicaid: kind medicaid; 835 name HUMANA CARE PLAN, payer id 61101, KY.
- Farm Bureau Health Plans Mapd: no 835 identities.
- Secure Horizons: no 835 identities (old PacifiCare/UnitedHealthcare MA brand).

## Humana
### Grievances and Appeals/Inquiry Directory (code 954306ALL0226; PDF created 2026-07-21)
URL: https://assets.humana.com/is/content/humana/Information%20for%20Non%20Participating%20Providerspdf (sources/humana_nonpar_info.pdf)
- Claim payment inquiries/disputes: Availity Essentials (Claim Status > Dispute Claim > Appeals worklist), phone 800-448-6262,
  par-only escalation by secure email HumanaProviderServices@Humana.com. No fax.
- Medicare Advantage grievances and appeals: "Participating providers do not have grievance and appeal rights on behalf of
  themselves ... Nonparticipating providers may appeal on behalf of themselves". "within 65 calendar days from the date of the
  denial notice". Mail: Humana Grievance and Appeal Department, P.O. Box 14165, Lexington, KY 40512-4165. Availity also. No fax
  in the provider directory. Nonpar on own behalf: signed Waiver of Liability Statement.
- Medicaid, Dual Demonstration and AIP appeals: state resources (Indiana, Kentucky links).
### Appeal or Grievance Form (member form, Y0040_GHHJX8LENa_C; PDF created 2026-02-10)
URL: https://assets.humana.com/is/content/humana/G-A_Form_Clean_2024pdf
- "may be sent to us by mail or fax: Address: Humana Grievance and Appeals Department P.O. Box 14165 Lexington, KY 40512-4165
  Fax Number: 888-556-2128". The provider directory tells providers to use this form (skip Section 4) but to mail it.
### Humana Healthy Horizons in Kentucky (Medicaid) provider G&A page
URL: https://provider.humana.com/medicaid/kentucky-medicaid/grievances-and-appeals
- Appeals: 60 calendar days from notice; claim reduction or denial 120 calendar days. Mail P.O. Box 14546 Lexington KY
  40512-4546; phone 800-444-9137; fax 800-949-2961; Availity. Form: Kentucky Medicaid MCO Provider Appeal Request Form (CHFS).
- Contracted rate disputes (a grievance): 24 months; mail Contracted Rate Disputes P.O. Box 14546; fax 800-949-2961; Availity.
- External independent review: 60 calendar days from receipt of the appeal resolution; mail P.O. Box 14546; fax 502-508-1796;
  email HumanaMedicaid_EIR_MFH@humana.com.
### Humana Healthy Horizons in Indiana (PathWays) claims page
URL: https://provider.humana.com/medicaid/indiana-medicaid/claims-payments
- Informal dispute: Availity; mail Attn: Informal Dispute PO Box 14169 Lexington KY 40512-4169; email
  INMedicaidClaimsResearch@humana.com; 60 calendar days of the notice. Formal dispute: mail 201 North Illinois Street Suite 1200
  Indianapolis IN 46204 or email IndianaFormalDispute@Humana.com; 60 calendar days after the 30-day informal process. No fax.
- Indiana D-SNP: "Humana PathWays Dual Care (HMO-POS D-SNP)"; ID card file named INFIDE (fully integrated D-SNP).

### Humana claim-payment inquiry resolution guide 642103ALL0125 (PDF 2025-07-14)
URL (file name says 2024, content is the 0125 version): https://assets.humana.com/is/content/humana/FINAL_412905ALL0224-A_HUMP0412905_Claim_Payment_Inquiry_Resolution_Guide_4.19.24pdf
(linked as "Download a detailed guide" from https://provider.humana.com/coverage-claims/payment-inquiries, live today)
- Disputes by mail: Humana Correspondence, P.O. Box 14601, Lexington, KY 40512-4601. Phone 800-448-6262. Availity.
- Par escalation: HumanaProviderServices@humana.com with the Claim Escalation Form.
- Nonpar appeals, Medicare: Availity or mail Humana Grievances and Appeals, P.O. Box 14165, Lexington, KY 40512-4165.
  "Medicare expedited fax 800-949-2961*" (expedited = pre-service only; not for claim payment). WOL required.
- Medicaid/dual Medicare-Medicaid: Availity or mail P.O. Box 14546 Lexington KY 40512-4546; "Medicaid/duals expedited fax
  855-336-6220*" (expedited only).
- Commercial: P.O. Box 14546; "Commercial standard fax 888-556-2128"; commercial expedited fax 920-339-2112.
- Records requested by PPI: Availity MRM; mail Humana Medical Records Management P.O. Box 14465 Lexington KY 40512-4465;
  "Fax: 866-305-6655". Medical record review disputes: P.O. Box 14279 Lexington KY 40512-4279; "Fax: 888-815-8912".
### CONCLUSION Humana MA fax: no fax for standard provider claim appeals or disputes.
- 888-556-2128 is on the member Appeal or Grievance Form (Medicare code Y0040) and in some 2026 member EOCs, but Humana's
  provider guide labels it "Commercial standard fax". The provider directory, the 2026 manual, the live
  reconsiderations-appeals page and the guide all say Availity or mail for MA. Do not offer a fax for MA appeals.
### Live provider page https://provider.humana.com/coverage-claims/payment-integrity/reconsiderations-appeals
- "Medicare Advantage plans: appeals for nonparticipating providers ... in writing, via Availity Essentials or mail, within 65
  calendar days from the date of the denial ... signed Waiver of Liability ... mail it to: Humana Inc. P.O. Box 14165
  Lexington, KY 40512-4165".
### Records fax 866-305-6655: three Humana sources agree (2026 manual p.20; guide 642103ALL0125 p.4; live page
  https://provider.humana.com/coverage-claims/payment-integrity/medical-records-management). Mail P.O. Box 14465.
### Records-review dispute fax 888-815-8912: live page medical-record-dispute-policy + guide p.8 agree. P.O. Box 14279
  (the Indiana Medicaid guide, Jan 2025, prints P.O. Box 14167 with the same fax). Level one: in-network 18 months,
  out-of-network 6 months; level two 60 calendar days from the level one letter.
### Claim Disputes and Corrected Claims Tip Sheet (NPC1055607ALL0826-B, PDF 2026-08-21), Humana MA
URL: https://assets.humana.com/is/content/humana/Claim%20Disputes%20and%20Corrected%20Claims%20Tip%20Sheetpdf
- Disputes: Availity Dispute Claim; phone; mail to the address on the back of the ID card. Next level (contracted only):
  HumanaProviderServices@Humana.com. Time frames: contracted 18 months; not contracted 180 days from original determination.
- Corrected claim: Availity "Correct this Claim"; 837P CLM05-3 = 7; paper CMS-1500 marked corrected or box 22 code 7 with the
  original claim number, mailed to the address on the back of the ID card; within 1 year from the original determination.
- Paper claim address: Humana Claims Office, P.O. Box 14601, Lexington, KY 40512-4601 (2026 manual p.8; PPO FAQ
  885803ALL1125-D "Humana MA PPO c/o Humana Claims Office P.O. Box 14601 Lexington, KY 40512-4601").

## UnitedHealthcare Medicare Advantage (payer id 87726)
### Non-contracted rights (PCA-1-24-03448-POE-QRG_01092025; PDF 2025-01-21; same bytes as the copy saved in lever/)
URL: https://www.uhcprovider.com/content/dam/provider/docs/public/health-plans/medicare/MA-Non-Cont-Provider-Dispute-Appeal-Rights.pdf
- Claim payment dispute (amount vs Original Medicare): within 120 calendar days from the initial payment; portal only
  (Claims & Payments > Look up a Claim > Act on a Claim > Explore available actions > Create an appeal/dispute). 30-day answer.
- Payment reconsideration (denial, or different service/level paid): 60 days from the denial date / "within 60 calendar days of
  the remittance notification date"; portal; Waiver of Liability Statement; 60-day answer; upheld cases forwarded to the IRE.
- No fax and no mail address in this document.
### 2026 UHC Administrative Guide (PDF 2026-05-13), Chapter 10 p.134-146
URL: https://www.uhcprovider.com/content/dam/provider/docs/public/admin-guides/2026-UHC-Administrative-Guide.pdf
- "You must submit your reconsideration and appeal to us within 12 months from the date of the original claim EOB or PRA"
- Step 1 reconsideration and step 2 appeal: online (portal) or API only. Notice of Dispute 60 days after the appeal decision.
- Corrected claims: EDI CLM05-3 = 7 + REF original reference; portal Claims & Payments > Act on a Claim > Submit corrected
  claim; paper: frequency code 7 in box 22 + original claim number. Timely filing of corrected claims = agreement's filing limit.
- Requested records/attachments: portal Claims & Payments, or API; audits: follow the request letter.
- Member appeals (to forward): MA/MAPD P.O. Box 6106 Mail Stop CA 124-0157 Cypress CA 90630; capitation supplement adds
  "Fax: 1-888-517-7113". This is the MEMBER appeals/grievances route, not a provider claim appeal channel.
### News "Feb. 1: Submit post-service appeals electronically" (PCA-1-25-02664-POE-NN_01072026, dated 2026-02-01)
URL: https://www.uhcprovider.com/en/resource-library/news/2026/paperless-submissions-february.html
- "beginning Feb. 1, 2026, you'll be required to submit all post-service appeals electronically" for network commercial and
  MA providers; exclusions list Colorado and others, not Kentucky or Indiana.
### Appeals page (live): https://www.uhcprovider.com/en/claims-payments-billing/appeals.html
- "Most network health care professionals ... UnitedHealthcare Medicare Advantage (including D-SNP) ... are required to submit
  reconsiderations and pre- and post-service appeals digitally. We also recommend that out-of-network health care
  professionals submit pre- and post-service appeals electronically." Portal: https://secure.uhcprovider.com/#/claims
### Contact page (live): https://www.uhcprovider.com/en/contact-us.html
- Kentucky and Indiana sections: phone only ("Medicare Advantage and Community Plan 800-600-9007"). The MA "Appeals and
  grievances" fax 888-517-7113 / urgent 866-373-1081 / P.O. Box 6111 MS CA 124-0157 appears only under Colorado.
### Group MA (PPO) out-of-network QRG (PCA-1-26-00083-POE-QRG_01282026; PDF 2026-02-09)
- "Paper claims: Mail claims to the address on the member ID card." Sample card: "Medical Claim Address: P.O. Box 31362, Salt
  Lake City, UT 84131-0362".
### CONCLUSION UHC MA fax: none for provider claim reconsiderations, appeals or disputes in KY/IN. Portal (or API) only.

## Anthem / BCBS Medicare Advantage (835 payer ids ANTHM, 00660, 00630)
### MA Provider Guidebook "January 1, 2027", footer "November 1, 2026" (Anthem Blue Cross and Blue Shield-CR-PM-017345-26-GRP4605)
URL (linked as "Download the Medicare Advantage manual" from both https://www.anthem.com/ky/provider/individual-commercial/policies/manuals
and https://www.anthem.com/in/provider/individual-commercial/policies/manuals): https://www.anthem.com/content/dam/digital/docs/provider/commercial/manuals/ABSCRPM-0010-20.pdf
(PDF modified 2026-10-24; same bytes as the copy in lever/.) No fax for any provider dispute or appeal; the only fax is prior auth 866-959-1537.
- 1.14 paper claims GA, IN, KY, MO, OH, WI: Anthem Blue Cross and Blue Shield, P.O. Box 105187, Atlanta, GA 30348-5187.
- 5.3 reconsideration (step 1): "in writing, verbally and through our provider website within 120 calendar days from the date on
  the Explanation of Payment (EOP)"; 45-day goal.
- 5.4 claim payment appeal (step 2): "through our provider website or in writing within 30 calendar days of the date on the
  reconsideration determination letter"; 30-day goal.
- 5.5 how: verbal (reconsideration only) via Provider Services; Availity (Claim Status > Dispute Claim > appeals dashboard);
  written with the Claim Payment Appeal Form or the Reconsideration Form to "Anthem Blue Cross and Blue Shield ATTN: Medicare
  Payment Dispute Unit P.O. Box 105187 Atlanta, GA 30348-5187" (GA, IN, KY, MO, OH, WI).
- 5.5.3 claim correspondence (records asked for on the EOP, corrected claims, COB): Claim Correspondence Form + EOP copy to
  "ATTN: Claims Correspondence P.O. Box 105187 Atlanta, GA 30348-5187". Corrected claim "must be received within 365 days of the
  date of service" (if original was timely).
- 6.2 participating provider medical-necessity appeal: written, "within 180 days from the Anthem decision letter date"; mail
  MCAG Attention: Medical Necessity Provider Appeals, Mailstop OH0205-A537, 4361 Irwin Simpson Road, Mason, Ohio 45040; 60 days.
- 7.1 nonpar payment disputes use the section 5 process. 7.2 nonpar appeal (denial with member liability): "within 65 calendar
  days from the remittance notification", signed Waiver of Liability, written; mail MCAG Attention: Non-Contracted Provider
  Appeals, Mailstop OH0205-A537, 4361 Irwin Simpson Rd, Mason, Ohio 45040.
- 2.5 digital: "expected to use available digital and electronic self-service applications instead of manual channels,
  including paper, mail, fax, phone, and chat"; 2.5.4 attachments: X12 275, Availity Claim Status, Medical Attachments (Digital RFAI).
- Paper fee: $5 per paper claim (not for D-SNPs/FIDE SNPs).

## CMS second level (non-contracted MA): Part C IRE
- eCFR 42 CFR 422.582(b) as of 2026-11-19: "a request for reconsideration must be filed within 60 calendar days after receipt of
  the written organization determination notice"; receipt presumed 5 calendar days after the notice date (so 65 days from the
  notice date). 422.590(b): payment reconsiderations decided within 60 calendar days; adverse ones go to the IRE automatically.
- CMS page https://www.cms.gov/medicare/appeals-grievances/managed-care/review-part-c-independent-entitiy (modified 2026-05-01):
  plan must auto-forward adverse reconsiderations; "Effective May 1, 2026, C2C Innovative Solutions, Inc. (C2C) is the Part C
  IRE." A non-contract provider may ask the IRE to review a plan DISMISSAL within 65 calendar days, in writing "which includes by
  fax": C2C ... Attn: QIC Part C - New Plan Dismissal Appeal, P.O. Box 1949, Jacksonville, FL 32231-0053; Fax (904) 539-4080;
  phone 1-866-320-0963. IRE decides payment requests within 60 calendar days.
- Second check: C2C page https://partcappeals.c2cinc.com/Enrollees-and-Representatives/How-to-Request-Review-of-Plan-Dismissal
  shows the same box and "Fax for Enrollees to Submit New Request for IRE Review of Plan Dismissal 904-539-4080".

## Wellcare (Centene) Medicare Advantage
Payer ids (claims guide): KY 14163; IN 68069 (matches the 835 "AMBETTER 68069" identity in the WellCare MA group).
### 2026 MA Provider Manual (PDF 2026-04-29) https://www.wellcare.com/-/media/pdfs/na/provider/resources/na_medicare_advantage_provider_manual_2026_r.ashx
- Section 7 claim payment disputes: portal preferred; mail "to the address listed on the back of the member's ID card"; timeframe
  per contract. Corrected claims: CLM05-3 = 7 + REF F8; paper box 22 frequency code 7 + original claim number; within the 180-day
  timely filing (initial, corrected and voided) unless the agreement says otherwise.
- Section 9 participating provider reconsiderations: 90 calendar days from the denial; portal preferred; mail KY P.O. Box 31368
  Tampa FL 33631-3368, IN P.O. Box 3060 Farmington MO 63640-3060; "Fax: Send this form ... to fax number 1-866- 201-0657 for
  Wellcare and 1-844-273-2671 for Wellcare By Allwell" (Allwell rows are AR, MO, TX only).
### Provider Cover Sheet "Claim Appeals, Reconsiderations & Disputes" (5639862_NA5PCARFRME, approved 01/06/2026, PDF 2026-04-14)
URL https://www.wellcare.com/-/media/pdfs/na/provider/forms/other/na_care_prov_appeals_form_2026_r.ashx
- Appeal/Reconsideration: Attn: Appeals Department, Wellcare Health Plans, Inc., P.O. Box 31368, Tampa, FL 33631-3368.
  Claims Payment Dispute: Attn: Claims Payment Dispute, P.O. Box 31370, Tampa, FL 33631-3370.
  "You may also fax the Appeal/Reconsideration request to 1-866-201-0657." Par and Non-Par boxes; WOL for non-par.
### Appeals/Reconsiderations (Medical) and Grievances Guide (Jan 2026 and Aug 2026 editions)
URLs .../na_care_prov_appeals-guide_2026_r.ashx and ..._2027_r.ashx (linked from https://www.wellcare.com/en/kentucky/providers/medicare)
- Non-par: 65 calendar days from the notice + signed WOL (+ AOR if a vendor files). Par: 90 calendar days.
- "MAIL OR FAX ALL MEDICAL APPEALS AND RECONSIDERATIONS WITH SUPPORTING DOCUMENTATION TO:" KY row P.O. Box 31368 Tampa; IN row
  P.O. Box 3060 Farmington; "Fax: 1-866-201-0657".
### Claims, Disputes & Recovery/CCU Guide (Jan 2026 and Aug 2026 editions) .../na_care_prov_claims-guide_2026_r.ashx, _2027_r.ashx
- Disputes: "within 90 calendar days of the date on the EOP"; portal or mail; "MAIL OR FAX ALL CLAIM PAYMENT DISPUTES, MEDICAL
  APPEALS AND RECONSIDERATIONS ..." same KY/IN boxes, "Fax: 1-866-201-0657".
- Paper claims: KY P.O. Box 31372 Tampa FL 33631-3372; IN P.O. Box 3060 Farmington MO 63640-3060. Timely filing 180 days.
### CONCLUSION Wellcare MA fax 1-866-201-0657: four Wellcare publications agree, for KY and IN, appeals and reconsiderations
(par and non-par), and (claims guide) claim payment disputes. Conflict: the cover sheet sends disputes to P.O. Box 31370 and
names fax only for appeals; the claims guide routes disputes to the Appeals P.O. Box and the same fax.

## Dual eligibles
### Humana Medicare Advantage Medicaid (EMA group; kind medicaid; 835 HUMANA CARE PLAN 61101 KY)
- Kentucky D-SNP: 2026 Summary of Benefits H1036-320: "Humana Gold Plus SNP-DE H1036-320 (HMO D-SNP) ... This is a Highly Integrated
  Dual Eligible (HIDE) Special Needs Plan" with "a Medicare contract and a contract with Department for Medicaid Services (DMS)".
  No source shows exclusively aligned enrollment in KY, so unified appeals are not shown to apply; Medicare part = Humana MA routes.
- Kentucky Medicaid part (Humana Healthy Horizons in Kentucky): appeals fax 800-949-2961 (live G&A page + 2025 KY provider manual
  contact table "Provider Grievance and Appeals ... P.O. Box 14546 ... Fax: 800-949-2961"); rate disputes same fax (page + form);
  EIR fax 502-508-1796 (page + EIR form), email HumanaMedicaid_EIR_MFH@humana.com. Window conflict: page 60 days / 120 days for
  claim reduction or denial; manual 60 days from original adjudication.
- Indiana: Humana PathWays Dual Care (HMO-POS D-SNP); Indiana waiver (IN.017.00.03, Jan 2026): MCEs must keep a statewide FIDE D-SNP,
  exclusively aligned enrollment from 2026-01-01 -> applicable integrated plan (42 CFR 422.561); integrated reconsideration 60 days
  after receipt, plan decides in 30 days (422.633); assignee provider with waiver is a party (422.629(l)(4)). Humana's 2026 IN D-SNP
  provider resource guide PDF is a "Coming Soon" placeholder. Indiana Medicaid disputes: informal (Availity, P.O. Box 14169, email
  INMedicaidClaimsResearch@humana.com, 60 days) then formal (201 N Illinois St Suite 1200 Indianapolis / IndianaFormalDispute@humana.com,
  60 days after the 30-day informal step). Humana guide: Medicaid/dual appeals Availity or P.O. Box 14546; fax 855-336-6220 expedited only.
### United Healthcare Dual Complete
- UHC payer list (May 2026): "UnitedHealthcare Community Plan / UnitedHealthcare Dual Complete (formerly Evercare) 87726".
- KY plans: KY-S001, KY-S002, KY-S3, KY-S4, KY-V001 (UHC KY D-SNP provider page). KY-S4 FAQ: UHC also crosses over and pays eligible
  Kentucky Medicaid services ("you may receive 2 provider remittance advices"). IN: IN-S002 (full duals not PathWays-eligible), IN-D001
  (partial duals); UHC PathWays Dual Care IN-S1/IN-S3 are the aligned FIDE plans.
- UHC appeals page: network providers for "Medicare Advantage (including D-SNP)" must submit reconsiderations and appeals digitally.
  UHC 2026 MA, CSNP and DSNP QRG: appeals online (Claims & Payments > Look Up a Claim > Act on Claim > Appeal) or API.

## Farm Bureau Health Plans Mapd
- Tennessee Farm Bureau Health Plans; MA and Part D pages on fbhealthplans.com return 404 today. Provider page (modified 2025-12-05):
  "Medicare Supplement or Medicare Advantage Plan claims are processed by Farm Bureau Health Plans." Provider line 844-874-8301.
  No MA appeal channel or fax published now. Search snippets (not proof) said MA ended for 2025.

## Secure Horizons
- UHC payer list (May 2026): "UnitedHealthcare Medicare / UnitedHealthcare MedicareComplete (formerly SecureHorizons) 87726" and
  "AARP MedicareComplete insured through UnitedHealthcare (formerly AARP MedicareComplete from SecureHorizons)". Use UHC MA routes.

## Ambetter (only if the card says Ambetter; payer 68069 shared with Indiana Wellcare MA)
- Indiana QRG (Sep 2026) and Kentucky QRG (Oct 2026): "Appeals and Grievances Fax: 1-833-886-7956", P.O. Box 10341 Van Nuys CA 91410.
  Indiana appeals guide (Jan 2025) same fax. Disputes: portal or Attn: Claims Disputes/Appeals P.O. Box 5010 Farmington MO 63640-5010.
  Windows: IN par/non-par 180 days; KY par 180, non-par 90.

## Summary of fax numbers found (each with two or more official sources)
- Wellcare MA appeals/reconsiderations (and disputes per claims guide), KY and IN: 1-866-201-0657.
- Humana records requested: 866-305-6655. Humana record-review disputes: 888-815-8912.
- Humana Healthy Horizons in Kentucky (Medicaid side of KY duals): appeals 800-949-2961; EIR 502-508-1796.
- Ambetter appeals (marketplace, KY and IN): 1-833-886-7956.
- CMS Part C IRE, plan dismissal reviews only: 904-539-4080 (CMS + C2C).
## No fax for provider claim appeals: Humana MA (Availity/mail), UHC MA and Dual Complete (portal/API only), Anthem MA (Availity/mail),
   Humana Indiana Medicaid/PathWays (Availity/mail/email), Farm Bureau MAPD (nothing published), Secure Horizons (UHC portal).
## Output: research.json built by ../medicare_advantage_scripts/build_json.py, which checks every quote against the saved sources.

## Output schema check (2026-11-23)
The return schema takes only fax, portal, mail, email or phone as a channel. Facts that are not channels (the Kentucky D-SNP's HIDE status,
the UHC payer-list identities for Dual Complete and SecureHorizons, Farm Bureau processing its own MA claims, Indiana's exclusively aligned
enrollment and the 42 CFR 422.629 assignee rule) now sit in each entry's notes with their verified quotes and URLs. The Kentucky dual entry's
Medicare-part pointer is now a real Availity Dispute Claim channel quoted from Humana's claim payment inquiries page. The builder checks every
key, enum and type, and that each fax channel's digits appear in its own quote. Result: 15 entries, 77 channels, 16 fax channels.
