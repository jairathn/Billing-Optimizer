# CHANNELS research: commercial_national family (notes)

Read on 2026-11-23. Public pages and PDFs only, no logins. Downloads in sources/<name>/ (raw file plus doc.txt or
page.txt; Regence/Asuris pages also content.txt pulled from the page's embedded markdown). Fetch log: sources/fetchlog.txt.
Search terms used: payer names, product names, states only. Nothing about the practice or any patient was sent anywhere.

## Anthem / BCBS (835 payer ids ANTHM, 00660 = KY Blue Shield code, 00630 = IN Blue Shield code; payer name ANTHEM)
### Kentucky commercial manual (2026 v1, effective 9/1/2026; PDF 2026-07-30) sources/anthem_ky_pm
- Claim Payment Dispute = 2 steps. Step 1 reconsideration "via phone, Availity Essentials, or in writing"; "a minimum of
  two (2) years from the issue date of the EOP". Step 2 appeal "through Availity Essentials or in writing" within 90 days
  of the reconsideration determination.
- How to submit: "Online through the Availity"; mail "Anthem Blue Cross and Blue Shield Payment Dispute Unit P O. Box
  105557 Atlanta, GA 30348-5557" with "the Claim Payment Appeal Form or the Reconsideration Form"; phone on the ID card.
- No fax anywhere for claim payment disputes. "Anthem expects ... digital ... in lieu of manual channels (paper, mail,
  fax, call, chat, etc.)"; "It is the expectation of Anthem that electronic Claim payment disputes are adopted".
- Clinical appeals (medical necessity): Availity Authorizations & Referrals, verbal, or writing to "Attention: Grievances
  and Appeals P.O. Box 105568 Atlanta, GA 30348-5568"; 180 calendar days; no fax printed.
- BlueCard members: claim-denial appeals go to the LOCAL Anthem plan as a Claim Payment Dispute.
- Corrected claims: EDI 837 frequency 7 + REF*F8; Availity Claims & Payments (Replacement of Prior Claim); paper only
  "for a specific reason" (CMS-1500 box 22 code 7 + original claim number).
- Records Anthem asked for: 275 or Availity Claim Status "Send Attachment"; "If Availity Essentials set-up has not been
  completed and medical records must be sent via mail or fax, send them to the appropriate department as directed in
  the notification from Anthem." (no fixed fax). Within 10 calendar days of the request.
### Indiana commercial manual v1 (eff 8/1/2026) and v2 (eff 1/1/2027, PDF 2026-11-03) sources/anthem_in_pm12, _pm13
- Same two steps; "two (2) years from the issue date of the EOP"; appeal 90 days. Submit: "Online through Availity
  Essentials Claims Status Application (preferred method where available)"; mail "Anthem Claim Payment Dispute P.O. Box
  105557 Atlanta, GA 30348-5557" with "the Provider Adjustment Form"; phone. No fax. Clinical appeals P.O. Box 105568.
### KY Quick contact guide (QCG), PDF 2026-10-21, sources/anthem_ky_qcg (rendered pg-3.png checked by eye)
- Local products (Blue Access/Preferred/Traditional): paper claims "Medical claims Anthem P.O. Box 105187 Atlanta, GA
  30348-5187" and, in the same paper-claims cell, unlabeled "Fax: 800-376-0247". Appeals address "Attn: Grievance and
  Appeals P.O. Box 105568" (no fax). Provider Services 800-345-4344.
- BlueCard (non-Kentucky members' plans): appeals address "Anthem ITS/Blue Card Dept. P.O. Box 105557 Atlanta, GA
  30348-5557"; BlueCard Provider Service 866-594-0521; route to Home Plan 800-676-2583.
- Medicare Supplement products: appeals "Attn: Appeals Department P.O. Box 105568 ... Fax: 888-859-3046".
- FEP: appeals "Anthem Federal Employee Program P.O. Box 105557" (no fax). Anthem associates HRA only: "Or fax: 877-876-4992".
### Ohio QCG (PDF 2026-10-22) sources/anthem_oh_qcg: Ohio local products G&A box 105568 "Fax: 888-859-3046 (non-urgent)";
  FEP Ohio non-clinical appeals P.O. Box 105568 fax 888-859-3046; Medicare Supplement fax 888-859-3046.
  -> 888-859-3046 is the Central-region Grievances and Appeals fax. Published for KY only on the Medicare Supplement row.
### Indiana Quick Reference Guide UPDATED 11-19-2019 (old; IN_CONTACT_0002.pdf; still hosted) sources/anthem_in_contact
- "Request for Claim Review, Adjustment or Submitting Records: Anthem BCBS PO Box 105557 Atlanta, GA 30348-5557 Fax:
  1-800-376-0247. Include completed Provider Adjustment Request Form, or a letter with all pertinent claim and patient
  details." (crop_review.png). National accounts and Medicare Supplement appeals fax 888-859-3046 (2019).
- CONCLUSION: 800-376-0247 is NOT offered: the 2026 manuals list no fax for disputes; the 2026 KY QCG prints the number
  unlabeled in the paper-claims cell; the only label is from 2019. A person must confirm with Anthem before any use.
### BlueCard manual (KY, "January 1, 2022"; PDF mod 2024-09-03; linked from the 2026 KY manual) sources/anthem_bluecard_ky
- 4.10 "Contact your local Anthem Plan if an adjustment is required." 4.11 "Appeals for all claims are handled through
  your local Anthem Plan. We will coordinate the appeal process with the member's BCBS Plan, if needed."
- Records for out-of-area claims: send to the local Anthem plan as the request letter directs; pre-auth records go to the
  member's plan.

## Asuris Northwest Health (no 835 identities). NOT a BlueCard plan for KY/IN care.
- Member travel page: "you have access to Multiplan for health care when you travel outside your local Asuris service
  area" (sources/asuris_travel/content.txt). So the claim goes to Asuris itself, not through Anthem.
- Provider appeals (contracted providers only): Availity Essentials Appeals application. "As of March 1, 2026, the
  provider appeals fax line is deactivated"; fax only under exception criteria, to (888) 487-0939 with the Provider Fax
  Submission Exception Form; certified mail Asuris Attention: Appeals P.O. Box 1248 Lewiston ID 83501-1248.
  Window 24 months after payment or denial notice (30 months with COB); level two 90 days. (Admin manual "Appeals for
  Providers", revised April/June 2026.)
- Non-contracted providers: "Appeals by non-contracted providers may be eligible for the member appeal process." Member
  appeal form (FORM 4581AS, eff. 11/2023, linked from the live member appeals page): Commercial and Individual "Attn:
  Asuris Level 1 Member Appeals PO Box 1106 Lewiston, ID 83501-1106 or via fax at 1 (888) 496-1542"; Self-funded (ASO)
  fax 1 (877) 663-7526; MemberAppeals@asuris.com; 180 days; needs the patient's signature or HIPAA authorization.
- Line on the appeals page: "Out of area- non-contracted providers should submit rate negotiation requests according to
  their local Blue Plan billing dispute process." (No Surprises Act rate negotiation section; conflicts with the travel page.)

## UnitedHealthcare commercial (835 payer id 87726)
- Appeals page (live): network providers "are required to submit reconsiderations and pre- and post-service appeals
  digitally"; 2 steps within 12 months; Provider Portal or API; 20+ same-issue claims -> Claims Research Project.
- News Feb 1, 2026 (PCA-1-25-02664-POE-NN_01072026): "beginning Feb. 1, 2026, you'll be required to submit all
  post-service appeals electronically" for network commercial and MA; exclusions list (Colorado, UMR, Golden Rule, ...).
- 2026 Admin Guide ch.10 sec.5: "within 12 months from the date of the original claim EOB or PRA"; portal or API only;
  "Obligation to do business with UnitedHealthcare electronically". Member appeals for commercial plans: P.O. Box 30573
  Salt Lake City UT 84130-0573 (member forwarding table, not provider disputes). No commercial provider appeal fax.
- Single Paper Claim Reconsideration form (PCA-1-25-00612-POE-FM_03312025): "If electronic submission isn't possible";
  one claim per form; mail "using the address listed on the PRA"; not for formal appeals.
- Corrected claims: EDI frequency 7 + REF02; portal "Submit corrected claim"; paper box 22 code 7 + original number.
- Records: "Use the claims tool in the UnitedHealthcare Provider Portal" or API; audit records per the request letter.

## Second checks added after the first build (2026-11-23)
- American Medical Security (835 payer id 81400). FlexWork's live contact page (flexwork-public.uhc.com/contact-us,
  sources/flexwork_contact) lists "Claims Submission Mailing Address FlexWork P.O. Box 31375 Salt Lake City, UT
  84131-0375 Fax: 1-801-478-7582": the same fax the 2026 UHC guide gives for FlexWork reconsiderations. An older UHC page
  ("Master Contact Us RTE", page metadata last modified Oct 7, 2022, not linked from the current Contact us page;
  sources/uhc_contact_rte) gives payer ID 81400 and the same box and fax for All Savers paper claims, and lists All Savers
  formal appeals at "Appeals Review P.O. Box 31371 ... Fax: 317-715-7648". That 317 number conflicts with the 2026 guide's
  1-801-478-5463 for the same box. Golden Rule's filing posted by the Maryland Insurance Administration (Jan 2023;
  sources/md_goldenrule_referral) lists "Grievance Administrator PO Box 31371 Salt Lake City, UT 84131-0371 Standard fax:
  (801) 478-5463": the independent second check for the appeal fax. UHC's live Contact us page (sources/uhc_contact_us)
  lists "All Savers Health Plan 800-291-2634" under Kentucky and Indiana, so 81400 claims here may be All Savers members.
  The 2022 All Savers supplement page now redirects to an error page; the Colorado small-business quick reference host has
  an expired certificate (not fetched; TLS checks stay on). Neither is used as proof.
- Humana 888-556-2128. Humana's Appeal or Grievance Form (Feb 10, 2026; Y0040_GHHJX8LENa_C, a Medicare member form;
  sources/humana_ga_request_form) prints "Humana Grievance and Appeals Department ... Fax Number: 888-556-2128". So the
  digits are Humana's G&A fax, but the July 2026 directory (954306ALL0226) lists only Availity and mail for commercial
  appeals. Kept as a listed fax with the conflict stated; a person should confirm before the first send.
- G.E.H.A 1-866-963-0156. The 2026 FEHB brochure on opm.gov (71-014, Section 8; sources/opm_geha_2026_71014) lists
  post-service appeals by the online form at geha.com/My-Member-Portal or mail to "Post-Service Appeals: G.E.H.A, PO Box
  21324, Eagan, MN 55121" and prints no appeal fax. A web search for the number found no other G.E.H.A publication. The fax
  stays SINGLE SOURCE; the box now has three sources.
- Medica 1 (801) 994-1076. A web search found no other Medica page with this number. Medica's own pages answer 403 to
  automated readers. An archived copy was not used. The fax stays SINGLE SOURCE.
- The build (commercial_national_scripts/build_json.py) now checks each fax's digits against its own quote and each
  second check's quote against its saved source, and requires applies_to to say "Second check" or "SINGLE SOURCE".
  Result: 14 fax channels; 9 with a verified second check (Anthem Medicare Supplement, Optum VA CCN, Aetna, Humana records,
  Humana commercial (digits only), Asuris exception fax, both 81400 faxes); SINGLE SOURCE: Asuris member route, Medica (2),
  G.E.H.A (3 groups).
- Removed the Anthem trace payer ids from the "states" text: one of them passes the NPI check digit, which the repository's
  public scan (tests/test_docs.py) would flag.

## Per payer summary (the remaining groups; full detail with quotes in research.json)
- Anthem Medicare Supplement (KY): appeals to Attn: Appeals Department, P.O. Box 105568, Atlanta, GA 30348-5568 or fax
  888-859-3046 (2026 KY quick contact guide; Ohio 2026 guide and the 2019 Indiana guide agree). Not for KY/IN commercial.
- Anthem FEP (KY): Anthem Federal Employee Program, P.O. Box 105557; no fax. FEP disputes are the member's (fepblue.org).
- UnitedHealthcare commercial (87726): Provider Portal or API only since Feb 1, 2026; 12 months; no fax. Single Paper
  Claim Reconsideration form only "If electronic submission isn't possible", mailed to the address on the PRA.
- Optum (UHC): two entries. VA CCN (Region 2 KY/IN): reconsideration by secure fax 1-877-666-6597, mail P.O. Box 202118
  Florence SC 29502 or secure email; Optum VA CCN manual and Nov 2026 claims guide agree. Optum-named UHC commercial
  claims: UHC Provider Portal; no fax.
- Aetna commercial (60054): reconsideration (first level) by Availity, phone 1-888-632-3862, or mail to Aetna Provider
  Resolution Team, PO Box 981106, El Paso TX 79998-1106 (Kentucky and Indiana use El Paso); 180 days. Appeal (second
  level) by Availity, mail to PO Box 14020, Lexington KY 40512, or fax 1-859-455-8650 with the Practitioner and Provider
  Complaint and Appeal Request form; 60 days from the reconsideration decision. Page and form agree on the fax.
- Cigna (62308, 80705): call customer service first; CignaforHCP claim reconsideration; or mail the Request for Health
  Care Professional Payment Review to the National Appeals Unit, PO Box 188011, Chattanooga TN 37422 (or the address the
  ID card points to); 180 days. No fax published.
- Humana commercial: disputes by Availity, mail (Humana Correspondence, P.O. Box 14601) or phone 800-448-6262, 18 months;
  escalation by secure email for participating providers; commercial appeals for the covered person by Availity or mail
  (P.O. Box 14546). Records: Availity MRM or fax 866-305-6655.
- Excellus BCBS (NY home plan): BlueCard; appeals go through the local Anthem plan (KY or IN). No fax.
- HealthLink: Anthem-owned network and pricing organization. Pricing disputes and pricing appeals by mail to HealthLink,
  PO Box 659987, San Antonio TX 78265 (pricing appeal within 30 days of the dispute answer); benefit and claim appeals go
  to the claims administrator named on the EOP. No fax.
- Medica (94265, Salt Lake City): 2026 Provider Claim Adjustment or Appeal Request form; mail Medica PO Box 30990, Salt
  Lake City UT 84130 or fax 1 (801) 994-1076 (SINGLE SOURCE); contracted-rate disputes by phone 1-800-458-5512.
- G.E.H.A (39026; three RemitWatch groups): post-service appeals for the member (Designation of Authorized
  Representative) to G.E.H.A Post-Service Appeals, P.O. Box 21324, Eagan MN 55121; fax 1-866-963-0156 (SINGLE SOURCE);
  6 months; OPM review within 90 days of the appeal decision.
- CareSource Marketplace KY and IN: Provider Portal, or by mail (Kentucky: Grievance and Appeals Department, P.O. Box
  2008, Dayton OH 45401; Indiana payment disputes: Attn: Quality Improvement, P.O. Box 8738, Dayton OH 45401-8738). No fax
  for claim disputes or appeals (a 2024 manual fax for clinical appeals is not offered).
- Health Alliance (Carle, IL; plans ended at the start of 2026): Provider Inquiry Portal only; 90 days.
- Health Plans Inc (MA TPA): Provider Appeal Form (non-HPHC) by mail to PO Box 5199, Westborough MA 01581; no fax.
- Principal (group dental and specialty): dental provider portal, claims mailbox and email; no fax.
