# Codes a payer almost never pays: why, and what would get them paid

The owner's decision 4 (rule 4.2), researched on 2026-11-26. Aggregates only (counts, dollars, codes, payer groups). No
patient data. The public facts (payer rules and their sources) are in `remitwatch/data/payer_code_rules.json`, which the
application reads; the evidence from the store is computed by each run, never kept in the repository.

## What was done

- Code: the integration tip `claude/ecstatic-fermi-orfcgo` at 197b2bc (read-only checkout).
- Data: a private copy of the world (the live store image of 2026-11-24 with EMA's four reports). One server cycle with
  detection, day 2026-11-24 (195 s, peak 1,053 MB, no EMA step errors). Then the independent auditor A16 on the same copy.
- The list: every item and pattern of a code that a payer pays on 5% or less of the lines it answered as primary since
  2025-01-01, as `detect.py` counts it (`_paid_share`, `fold_payer_policy`, and C5's EMA-only patterns in
  `ema:c5_left_out`), plus every open item whose unit the auditor A16 calls not payable under owner rule 4.2 (fix 6:
  a code the payer never or rarely pays, G2211 beside an E/M with modifier 25, a code not in the payer's fee schedule).
- For each payer group and code: lines answered as primary and paid (835s and EMA's books), the denial reasons (CARC and
  RARC), modifiers, place of service, the codes billed on the same claim, the month of service, and the diagnoses where a
  source carries them.
- Diagnoses: the 837s in the store keep only diagnosis pointers, not the codes. EMA's weekly charge-line export carries
  ICD-10 codes for part of the period (from 2025-05-20), so diagnoses are shown where it covers the line.
- Then each payer's published rule, fetched from the web and fetched a second time to check that the title, the link,
  the date and every quoted sentence matched (15 sources, all matched both times; see the last section).

## The answer in short

The list holds 25 payer and code pairs (21 with open items on the list, 4 with only items the auditor flags): 968 open
items, $209,966, of which 491 items and $154,318 sit on Work now. Four findings explain almost all of the money:

1. **Hyperthermia (77600) with surface radiation therapy, from 2026.** CPT 2026 created the surface radiation therapy
   codes 77436-77439, deleted 77401, and says not to report 77436-77439 together with 77600. Anthem now denies 77600 as
   included in the radiation service (or changes it to 77436 and pays nothing), and the Optum group denies it as not
   covered. 352 open items, $143,160, all on Work now but 11. This includes the 20 "code-change" items ($7,150) the
   auditor flagged. **Never payable for 2026 service dates; no note changes it.** Medicare still pays 77600 next to
   77436/77437 in 2026, which is a risk, not a win (see "Related findings").
2. **G6001 (ultrasound guidance) at UnitedHealthcare, 2024-2025.** UnitedHealthcare required prior authorization for
   G6001 until 12/31/2025, and its image-guidance policy (which its Medicare Advantage policy points to) said image
   guidance is not medically necessary for superficial treatment of skin cancer, including superficial radiation therapy,
   until that language was removed on 03/01/2026. UnitedHealthcare paid the rest of each course and denied G6001.
   550 open items, $61,330 (115 on Work now, $13,966). **Never payable for those dates.** G6001 no longer exists; from 2026
   the ultrasound guidance is 77439, once per course.
3. **G2211 at commercial payers.** Anthem (23 paid of 5,790 lines answered), UnitedHealthcare (1 of 589), UMR (0 of 351),
   UHC Integrated Services (10 of 551). UnitedHealthcare's and Anthem's published policies say G2211 is never paid
   separately. Almost no money sits on the list (the denials are $0 items or parts of whole-claim items), but thousands of
   lines a year are billed for nothing. **Never payable at Anthem and UnitedHealthcare commercial; unclear at UMR and UHC
   Integrated Services (no published rule found).** Medicare and the Medicare Advantage plans pay G2211, except when the
   E/M carries modifier 25 for a procedure (CMS rule).
4. **Kentucky and Indiana Medicaid fee-for-service.** Every denial is about the provider (not enrolled, credentialing,
   not affiliated with the billing group, taxonomy) or the payer (the member's managed care plan must be billed). **Fixable
   on the claim**, not the note. Small money on the list (14 items, $5,115).

Smaller pairs: Humana Medicare Advantage G0127 (nail trimming) needs the Q7/Q8/Q9 class-finding modifier and the note's
class findings (fixable); the J-code pairs carry $0; a few G2211 pairs have no published rule (unclear).

| Verdict | Pairs | Open items | Open $ | On Work now | Work now $ |
|---|---|---|---|---|---|
| never payable (by the cited rule) | 8 (Optum 77600 for 2026 dates) | 867 | $184,505 | 432 | $137,141 |
| claim (fixable on the claim) | 9 (Optum 77600 for 2025 dates) | 75 | $25,419 | 43 | $17,164 |
| unclear (no published rule found) | 9 (4 with only auditor items) | 26 | $43 | 16 | $13 |

(The Optum 77600 pair is split by service date: its 2026 items are never payable, its 2025 items depend on the VA
referral. The Medicaid 77600 pair's open items are all 2025 services, so it counts as fixable.) Nothing here changes what RemitWatch does with these items: all stay pursued until the owner accepts a
pattern on the policy page (the owner's decision 4).

## How each pair was found

| Payer group | Code | Found by | Paid / answered (835s, since 2025) | Open items | Open $ | Work now |
|---|---|---|---|---|---|---|
| UnitedHealthcare Medicare Advantage (EMA: UHC Medicare Advantage) | G6001 | fold_payer_policy, C5, A16 rarely paid | 8 / 347 | 523 | $57,200.17 | 94, $10,754.21 |
| UnitedHealthcare | G6001 | fold_payer_policy | 0 / 30 | 27 | $4,129.38 | 21, $3,211.74 |
| Anthem / BCBS | 77600 (billed; 2026 adjudicated as 77436) | A16 rarely paid (product level) | 176 / 298 as 77600; 0 of 189 recoded lines | 206 | $73,283.40 | 206, $73,283.40 |
| Optum (UHC) | 77600 | C5 (EMA's books) | EMA: 3 / 232 | 146 | $69,876.93 | 135, $65,944.65 |
| Medicaid (other) (KY Medicaid) | 77600 | C5, _paid_share | 0 / 20 | 7 | $4,657.08 | 2, $776.18 |
| Medicaid (other) (KY and IN) | 88305 | C5, _paid_share | 0 / 32 | 1 | $65.08 | 0 |
| Medicaid (other) | 11102 | C5, _paid_share | 3 / 83 | 1 | $176.68 | 0 |
| Medicaid (other) | 11103 | _paid_share | 0 / 39 | 1 | $0 | 1, $0 |
| Medicaid (other) | 99203 | _paid_share | 1 / 48 | 1 | $56.47 | 1, $56.47 |
| Medicaid (other) | 99213 | _paid_share | 3 / 117 | 3 | $159.39 | 1, $16.47 |
| Anthem / BCBS | G2211 | _paid_share, A16 rarely paid | 23 / 5,790 | 1 (+13 auditor) | $0 (+$1,053.90) | 1 (+11, $858.15) |
| UnitedHealthcare | G2211 | _paid_share, A16 | 1 / 589 | 1 (+1 auditor) | $0 (+$96.44) | 1 |
| UMR | G2211 | _paid_share | 0 / 351 | 4 | $0 | 4 |
| UHC Integrated Services (Wausau) | G2211 | A16 (rarely paid; not in fee schedule) | 10 / 551 | 0 (+2 auditor) | +$209.48 | +1, $121.88 |
| Medicare KY (CGS J15) | G2211 | A16 (G2211 with E/M -25) | 1,420 / 1,944 | 2 (+2 auditor) | $3.34 (+$85.60) | 2 |
| UnitedHealthcare Medicare Advantage | G2211 | A16 (G2211 with E/M -25) | 235 / 267 | 6 (+2 auditor) | $183.23 (+$194.98) | 6 |
| Aetna | G2211 | A16 (G2211 with E/M -25) | 53 / 176 (39 of the 53 "paid" were $0.01-$0.02) | 4 | $29.90 | 2, $0 |
| Medicaid KY / Medicaid IN | G2211 | A16 | KY 0 / 77; IN 0 / 2 | 0 (+4 auditor) | +$530.36 | 0 |
| Self-funded plan TPAs | G2211 | A16 (product level) | 166 / 286 | 0 (+2 auditor) | +$273.50 | 0 |
| A TPA outside the code's payer rules (one payer name) | G2211 | A16 | none answered | 1 | $12.91 | 1 |
| Humana Medicare Advantage | G0127 | A16 never paid | 0 / 11 | 7 | $132.02 | 4, $75.44 |
| Anthem / BCBS | J2327 | A16 never paid | 1 / 15 | 13 | $0 | 9 |
| Medicare KY (CGS J15) | J3490 | A16 never paid | 0 / 4 | 9 | $0 | 0 |
| Anthem / BCBS Medicaid | J3490 | A16 never paid | none since 2025 | 4 | $0 | 0 |

"+N auditor" items are whole-claim or never-answered items whose G2211 unit the auditor calls not payable; the G2211 part is
a small piece of each.

Group-level pairs with 5% or less paid but no item and no pattern (not on the list, for reference): Medicare KY 99024
(0 / 597) and 99024 at every payer (post-operative visit, never paid by design), Cigna G2211 (0 / 130), Freedom Life G2211
(0 / 33), Humana Medicare Advantage Medicaid G2211 (0 / 25), Medicare IN J3590 (0 / 43), UnitedHealthcare J3590 (0 / 28),
Kentucky Medicaid 17000, 17003, 17110, 77436, 77437 (no item on the list; not examined here).

## 1. Hyperthermia 77600 with surface radiation therapy

### What the data shows

- The radiation program's codes by service year (all payers): 77401 billed 5,506 times in 2024 and 5,498 in 2025, none in
  2026. G6001: 3,036 (2024), 5,938 (2025), none in 2026. 77280 and 0394T: none in 2026. 77436: 4,223 and 77437: 3,850 in
  2026 only. 77600: 1,993 (2024), 5,293 (2025), 3,240 (2026). So in 2026 the practice moved to the new codes but kept
  billing 77600 with them.
- **Anthem / BCBS.** 2025: 77600 paid on 146 of 177 local-plan lines and 19 of 30 BlueCard lines (no modifier; billed with
  77401, 77280 and G6001). 2026: Anthem paid 11 of 90 lines billed as 77600 (5 of the 11 carried modifier 59) and changed
  77600 to 77436 on 189 more lines, paying $0 on all of them. The unpaid lines say CO-97 (216 lines; "included in the
  payment for another service") with remark N19 (190; "procedure code incidental to primary procedure"); the rest CO-45,
  PR-27, OA-18, CO-29. Of the 311 unpaid lines, 268 were billed with 77436 and 77437 on the same claim. Place of service
  11 (office) or blank. Diagnoses where EMA's export carries them (16 lines): C44.311, C44.42, C44.729, C44.229 (basal or
  squamous cell skin cancers). Median charge $861, median payment when paid (2025) $322.
- **Optum (UHC)** (EMA's group; its postings come from the VA community care network through Optum, a VA payer posted
  under another name, and an Optum UnitedHealthcare payer that pays like Medicare, for example G2211 on 144 of 151 lines).
  77600: 3 paid of 232 answered lines, 2025 and 2026 alike. Reasons: CO-96 or OA-96 non-covered (159 lines) and CO-B15 or
  OA-B15 (54; "requires a qualifying service ... not received or adjudicated"), CO-29 timely filing (13). The same payers
  pay 77280, 77401, G6001, 77436 and 77437 most of the time. Billed with 77280, G6001, 77401 (2025) or 77436 and 77437
  (2026).
- **Kentucky Medicaid.** 20 lines from June to August 2026, all whole-claim denials (status 4): CO-96 with remarks N288,
  N570, N198, N767, N255 (enrollment, credentialing, affiliation, taxonomy) and claim remarks MA01, MA07, N807. The 7 open
  items are 2025 services that EMA shows denied for timely filing (CO-29).
- **Other payers, 2026, for contrast:** Medicare KY paid 1,086 of 1,145 77600 lines (the unpaid were mostly deductible and
  coinsurance), Humana Medicare Advantage 345 of 486 (64 of the rest denied for prior authorization, CO-197), UnitedHealthcare
  Medicare Advantage 45 of 48, UnitedHealthcare commercial 32 of 91 (55 denied CO-234, "not paid separately").

### The rule

- CPT 2026 (AMA, "Errata and Technical Corrections – CPT® 2026", dated June 2, 2026; the surface radiation therapy entries
  posted 12/12/25; effective January 1, 2026):
  "(77401 has been deleted. For surface radiation therapy, see 77436, 77437, 77438, 77439)" and
  "(Do not report 77436, 77437, 77438, 77439 in conjunction with 77261, 77262, 77263, 77280, 77285, 77290, 77295, 77300,
  77306, 77307, 77316, 77317, 77318, 77331, 77332, 77333, 77334, 77387, 77402, 77407, 77412, 77427, 77431, 77432, 77435,
  77469, 77470, 77499, 77600, 77605, 77610, 77615, 77620, ...)".
  https://www.ama-assn.org/system/files/2026-cpt-corrections-errata.pdf
- Medicare covers the service itself: NCD 110.1 "Hyperthermia for Treatment of Cancer" (effective 12/31/1984): "Local
  hyperthermia is covered under Medicare when used in connection with radiation therapy for the treatment of primary or
  metastatic cutaneous or subcutaneous superficial malignancies. It is not covered when used alone or in connection with
  chemotherapy." https://www.cms.gov/medicare-coverage-database/view/ncd.aspx?NCDId=66 . This is coverage, not coding: it
  does not override CPT's instruction for a commercial payer.
- VA community care (for the Optum group's VA postings): VA "Standardized Episodes of Care - Community Care" (last updated
  September 9, 2026): "Services outside scope of the authorized referral require additional VA approval before care is
  provided." and "Claims are processed in accordance with CMS National Correct Coding Initiative (NCCI), Medically
  Unlikely Edits (MUE), and other related payment edits."
  https://department.va.gov/vha/community-care/care-coordination/standardized-episodes-of-care/

### Verdicts

- **Anthem / BCBS, 77600: never payable** for 2026 service dates (all 206 open items are 2026). CPT says not to report it
  with 77436-77439; Anthem applies that. Modifier 59 got 5 lines paid, but using a modifier to get around a CPT "do not
  report" note is a recoupment risk, not a fix. The 20 code-change items ($7,150) are part of this pair: the auditor is
  right that they are not payable.
- **Optum (UHC), 77600: never payable** for 2026 dates (101 items, $49,705; CPT). For 2025 dates (45 items, $20,172; 34 on
  Work now, $16,240): payable only if the VA referral (its episode of care) or an approved Request for Service covered
  hyperthermia; VA requires that approval before care, so a missing approval cannot be fixed afterwards. What to check:
  the referral for that course.
- **Kentucky Medicaid, 77600: fixable on the claim** for 2025 dates (timely filing proof, enrollment; see section 4); never
  payable with 77436-77439 for 2026 dates.

### What the note or claim must show

Nothing in a note makes 77600 payable on the same course as 77436-77439. 77600 can be reported only with a radiation service
outside 77436-77439, and the note must name that service and its date. Stop billing 77600 with the surface radiation codes
from 2026 (all payers, see "Related findings").

## 2. G6001 ultrasound guidance at UnitedHealthcare (2024-2025)

### What the data shows

- **UnitedHealthcare Medicare Advantage:** 8 paid of 347 answered lines in 2025 (5 in April, 2 in May, 1 in June; median
  payment $100.76 on a $152.98 charge). The 339 unpaid: CO-197 (no prior authorization, 203 lines) and CO-272 ("coverage
  or program guidelines were not met", 136). Modifier 59 on all but one line. Place of service 11. Billed with 77280 (315),
  77401 (303), 77600 (297). On 331 of the 339 claims UnitedHealthcare paid another line of the same claim (the treatment
  itself), so it paid the course and refused only the guidance. Plans: mostly group retiree plans and AARP
  Medicare Advantage plans. No diagnosis in EMA's export for these lines.
- **UnitedHealthcare commercial (Choice Plus):** 0 of 30 paid, all CO-197 with CO-45; claim remark MA15 on 3.
- Open items: 523 (Medicare Advantage; 166 for 2024 services, mostly past the appeal window, 357 for 2025) and 27
  (commercial, 2025). The auditor calls 249 of the Medicare Advantage items already done (payer rarely pays the code).
- For contrast: Medicare KY paid G6001 on 2,154 of 2,224 lines, Humana Medicare Advantage 450 of 474, Anthem commercial
  153 of 267, Anthem Medicare Advantage 46 of 354.

### The rule

- UnitedHealthcare, "New simplified radiation therapy prior authorizations" (December 23, 2025): "This applies to
  UnitedHealthcare® Medicare Advantage Plans, UnitedHealthcare Community Plans, UnitedHealthcare commercial plans and
  UnitedHealthcare Individual Exchange plans." ... "On Jan. 1, 2026, the following codes will no longer require prior
  authorization:" ... "77014, G6001, G6002 and G6017" (image-guided radiation therapy) ... "the termed IMRT, IGRT and
  Standard Radiation Therapy 2D/3D procedure codes".
  https://www.uhcprovider.com/en/resource-library/news/2026/simplified-radiation-therapy-pa.html
- UnitedHealthcare Medicare Advantage Medical Policy "Radiation and Oncologic Procedures" (MMP077.17, effective September 1,
  2026, the current version): "Medicare does not have an NCD for IGRT. LCDs/LCAs do not exist." and "For coverage
  guidelines, refer to the UnitedHealthcare Commercial Medical Policy titled Radiation Therapy: Fractionation,
  Image-Guidance, and Special Services."
  https://www.uhcprovider.com/content/dam/provider/docs/public/policies/medadv-mp/radiation-oncologic-procedures.pdf
- UnitedHealthcare Commercial Medical Policy "Radiation Therapy: Fractionation, Image-Guidance, and Special Services"
  (2026T0613L, effective March 1, 2026), Policy History 03/01/2026: "Removed language indicating image-guided radiation
  therapy (IGRT) is not medically necessary for superficial treatment of skin cancer including superficial radiation
  therapy or electronic brachytherapy when the [listed] criteria are not met".
  https://www.uhcprovider.com/content/dam/provider/docs/public/policies/comm-medical-drug/radiation-therapy-fractionation-image-special-services.pdf
  (The earlier versions themselves could not be fetched: the archive site is blocked here. This revision note is
  UnitedHealthcare's own record of what they said.)
- CPT 2026 (same errata): 77439 "superficial or orthovoltage, image guidance, ultrasound for placement of radiation therapy
  fields for treatment of cutaneous tumors, per course of treatment (List separately in addition to code for primary
  procedure)".

### Verdict: never payable (both pairs)

For superficial radiation therapy of skin cancer with service dates before 2026, UnitedHealthcare's criteria made the
guidance not medically necessary, and it needed an authorization the practice did not have. A note cannot change that.
The one exception to check: if an approved authorization for the course listed G6001 (the 8 paid lines suggest some did),
resubmit with the authorization number.

### What the note or claim must show

Nothing for past dates. For 2026: report 77439 (never G6001) once per course with 77437 or 77438, and include it in the
authorization request.

## 3. G2211 (visit complexity add-on)

### What the data shows

| Payer group | Paid / answered | Main reason on unpaid lines | E/M with modifier 25 on the same day (unpaid / paid) |
|---|---|---|---|
| Anthem / BCBS | 23 / 5,790 | CO-97 (5,588) with N19 (5,253) | 283 / 0 |
| UnitedHealthcare | 1 / 589 | CO-234 (571) with M80 (505), M15 (66) | 20 / 0 |
| UMR | 0 / 351 | CO-234 (172) with M80 (209); PI-B10 (136) | 11 / 0 |
| UHC Integrated Services (Wausau) | 10 / 551 | CO-45 (228), CO-234 (145), PI-B10 (104); M80 (350), N448 (9) | 15 / 0 |
| Medicare KY (CGS J15) | 1,420 / 1,944 | CO-234 (183) with N20 (171); most of the rest went to the deductible (PR-1, 295) | 169 / 1 |
| UnitedHealthcare Medicare Advantage | 235 / 267 | CO-B15 (15), N122 (5), CO-97 (5) | 20 / 0 |
| Aetna | 53 / 176 | CO-97 (120) with N19 (117), on commercial plans; 52 of the 53 "paid" lines are Aetna Medicare plans, 39 of them at $0.01-$0.02 | 0 / 2 |
| Medicaid KY | 0 / 77 | CO-96 (39), CO-170 with N95 (22), B7 and enrollment remarks | 12 / 0 |
| Medicaid IN | 0 / 2 | CO-204 with M51, N130, N30, N34 | 0 / 0 |
| Self-funded plan TPAs | 166 / 286 | CO-45 (85), CO-234 (14), CO-96 (15) | 3 / 0 |

- Anthem's 23 paid lines were all BlueCard host claims (17, members of other Blue plans) or the Federal Employee Program
  (6). No local Anthem plan paid G2211.
- G2211 lines carry no modifiers of their own; place of service 11. They ride on 99213 and 99214 mostly. Diagnoses where
  EMA's export carries them: L70.0 (acne), L40.0 (psoriasis), Z08 with Z85.828 (follow-up after skin cancer), L20.89,
  L21.8, L30.9. The diagnoses do not separate paid from unpaid lines at any commercial payer.

### The rules

- UnitedHealthcare commercial: "Rebundling Policy, Professional" (2026R0056A; Q&A 13 added 9/1/2024): "Q: Will
  UnitedHealthcare Commercial and Individual Exchange allow separate reimbursement for codes G2211 and G0545 for visit
  complexity when billed on the same date of service for the same patient by the same physician or Other QHP with an
  Evaluation and Management Service?" "A: No, visit complexity for services G2211 and G0545 are structured in the
  reimbursement for evaluation and management services and not paid separately."
  https://www.uhcprovider.com/content/dam/provider/docs/public/policies/comm-reimbursement/COMM-Rebundling-Policy.pdf
- Anthem commercial: "Bundled Services and Supplies - Professional" (C-08003, last approval 09/03/2025, effective
  12/01/2025): "The health plan considers certain services and supplies to be ineligible for separate reimbursement when
  reported by a professional provider, unless provider, state, or federal contracts and/or requirements indicate
  otherwise." and "In addition, modifiers will not override the denial for the always bundled services and/or supplies
  listed in the embedded document." Its Section 1 grid lists "G2211 Visit complexity inherent to evaluation and management
  associated with medical care services that serve as the continuing focal point for all needed health care services".
  https://www.anthembluecross.com/content/dam/digital/docs/provider/commercial/policy/reimb/C-08003VA.pdf and
  https://www.anthem.com/content/dam/digital/docs/provider/commercial/general/bundled_services_section1_va.pdf
  Caution: these are the Virginia plan's copies. The Kentucky copy was not found online. Anthem's Kentucky denials match it
  exactly (CO-97 with N19 on every line), but ask Anthem Kentucky for its own Section 1 list.
- Medicare (and Medicare Advantage plans that follow it): MLN Matters MM13473 (revised April 29, 2025): "G2211 isn't
  payable when you report the associated office and outpatient E/M visit with modifier 25, except in certain cases
  starting January 1, 2025." and "G2211 is payable even if you report the base code with modifier 25 only when the service
  or other procedure requiring the reporting of modifier 25 is an allowed Part B service."
  https://www.cms.gov/files/document/mm13473-how-use-office-and-outpatient-evaluation-and-management-visit-complexity-add-code-G2211.pdf
- UMR, UHC Integrated Services, Aetna (commercial), Kentucky and Indiana Medicaid, self-funded administrators: no
  published rule found (Aetna's provider news could not be fetched from here).

### Verdicts

- Anthem / BCBS G2211: **never payable** (C-08003 Section 1; confirm the Kentucky copy).
- UnitedHealthcare G2211: **never payable** (Rebundling Policy Q&A 13).
- Medicare KY and UnitedHealthcare Medicare Advantage G2211 on a visit whose E/M carries modifier 25 for a procedure:
  **never payable** (MM13473). Otherwise these payers pay it. When the E/M itself was denied (B15 with N122), work the E/M.
- UMR, UHC Integrated Services, Aetna, Medicaid KY, Medicaid IN, self-funded administrators: **unclear**. Ask each for its
  written G2211 rule.

### What the note or claim must show

No note changes a payer's bundling rule. On the claim: do not report G2211 to Anthem or UnitedHealthcare commercial plans,
and never on an E/M with modifier 25 for a procedure (Medicare and Medicare Advantage), unless the modifier 25 is for an
allowed preventive service, an immunization or an annual wellness visit.

## 4. Kentucky and Indiana Medicaid fee-for-service (88305, 11102, 11103, 99203, 99213, and 77600 before 2026)

### What the data shows

Almost every line is a whole-claim denial (status 4). The reasons are about who billed, not what was billed:

| Code | Paid / answered | Reasons on unpaid lines | Remarks |
|---|---|---|---|
| 99213 | 3 / 117 | CO-96 (68), CO-B7 (36), CO-45 (12), CO-22 (10), CO-16 (7), CO-109 (4), CO-24 (3) | N570 (80), N198 (69), N255 (57), N288 (54) |
| 11102 | 3 / 83 | CO-96 (38), CO-B7 (26), CO-45 (11), CO-22 (9), CO-24 (8) | N570 (53), N198 (39), N288 (32), N255 (31) |
| 99203 | 1 / 48 | CO-96 (19), CO-B7 (16), CO-45 (10), CO-24 (6), CO-22 (3) | N570 (35), N255 (25), N198 (20) |
| 11103 | 0 / 39 | CO-96 (24), CO-B7 (18), CO-24 (3) | N570 (31), N198 (25), N288 (21) |
| 88305 | 0 / 32 | CO-96 (19), CO-B7 (9), CO-45 (7) | N570 (31), N255 (23), N198 (19), N767 (19) |
| 77600 | 0 / 20 | CO-96 (20) | N288, N570, N198, N767, N255 (20 each) |

Claim remarks MA01, MA07 and N807 on many: these are often Medicare crossover claims. EMA's books show the same codes paid
on most other Medicaid lines (for example 99213 paid on 16 of 18 EMA lines), so Medicaid pays the codes when the provider
side is right.

### The rule

- 42 CFR 455.410 (eCFR, text as of 2026-11-20): "The State Medicaid agency must require all ordering or referring
  physicians or other professionals providing services under the State plan or under a waiver of the plan to be enrolled
  as participating providers." and (d) "The State Medicaid agency must allow enrollment of all Medicare-enrolled providers
  and suppliers for purposes of processing claims to determine Medicare cost-sharing ...".
  https://www.ecfr.gov/api/versioner/v1/full/2026-11-20/title-42.xml?part=455&section=455.410
- The codes' meanings (X12): B7 "This provider was not certified/eligible to be paid for this procedure/service on this
  date of service."; N767 "The Medicaid state requires provider to be enrolled in the member's Medicaid state program prior
  to any claim benefits being processed."; N570 "Missing/incomplete/invalid credentialing data."; N198 "Rendering provider
  must be affiliated with the pay-to provider."; 24 "Charges are covered under a capitation agreement/managed care plan.";
  109 "Claim/service not covered by this payer/contractor. You must send the claim/service to the correct
  payer/contractor." https://x12.org/codes/claim-adjustment-reason-codes and
  https://x12.org/codes/remittance-advice-remark-codes

### Verdict: fixable on the claim

What the claim must show: a rendering provider enrolled with the member's state Medicaid and linked to the billing group,
with rendering and billing taxonomy codes; resubmitted within the filing limit (with proof of timely filing where EMA shows
CO-29). Reason 24 or 109: bill the member's Medicaid managed care plan. Reason 22: bill the primary payer first. From 2026,
77600 with 77436-77439 is not reportable at all (section 1).

## 5. Humana Medicare Advantage, G0127 (trimming of dystrophic nails)

- Data: 0 paid of 11 answered lines (2025). All CO-96 with N386 (NCD) and N115 (LCD). Modifier 59 on 7, none on 4; no
  Q7, Q8 or Q9 on any line. Billed with 99213, 17000, 11102, 17110. Open items 7 ($132.02), 4 on Work now ($75.44).
- Rule: CMS "Podiatry Care" (MLN compliance tips, last modified 02/11/2026) lists among the routine foot care Medicare does
  not cover "Trimming, cutting, clipping, or debriding nails", with the systemic-condition exception and the class
  findings presumption: "We may apply the coverage presumption when the physician providing the routine foot care has
  identified 1 of the following:" (one Class A finding, two Class B, or one Class B and two Class C).
  https://www.cms.gov/training-education/medicare-learning-networkr-mln/compliance/medicare-provider-compliance-tips/podiatry-care
  The contractors' billing articles require the class-findings modifier, for example WPS "Billing and Coding: Foot Care"
  (A56232, revision effective 10/01/2025): "All claims for routine foot care based on the presence of a systemic condition
  must have a billing modifier of Q7, Q8, or Q9 to be considered for payment."
  https://www.cms.gov/medicare-coverage-database/view/article.aspx?articleid=56232 (WPS covers Indiana; the Kentucky
  contractor's article was not retrieved).
- Verdict: **fixable on the claim, with the note behind it.** The note must name the systemic condition, the class findings
  found that day (or mycotic nails with pain, infection or limited walking), and where required the treating doctor of
  medicine or osteopathy and the date last seen. The claim must carry Q7, Q8 or Q9 to match, with the systemic condition's
  diagnosis.

## 6. The small pairs

- **Anthem / BCBS, J2327** (risankizumab, intravenous, per mg; billed with 96372, an injection under the skin or into a
  muscle): 1 paid of 15. Reasons CO-210 (authorization not timely), CO-197, CO-252, CO-16 with M119 (missing drug code),
  CO-4. Charges of $1.50 per line, so every item is $0. Verdict **unclear**; there is no money at stake. On the claim: the
  drug's authorization before the dose and the National Drug Code with the units. (Whether J2327, an intravenous code, is
  the right code for an injection under the skin was not checked.)
- **Medicare KY, J3490** (unclassified drug): 0 of 4; CO-16 with M23 (missing invoice) and MA130 (unprocessable), CO-250,
  CO-97 with N20. Items $0. Verdict **fixable on the claim**: a new claim with drug name, dose, National Drug Code and the
  invoice.
- **Anthem / BCBS Medicaid, J3490**: 4 items from 2023, $0, past every window. **Unclear.**
- **One TPA's G2211** (a payer name that no code rule groups): 1 never-answered item, $12.91. **Unclear.** Not in the data
  file (practice-specific payer name).

## Related findings for the owner

1. **Medicare and Medicare Advantage still pay 77600 next to 77436/77437 in 2026** (Medicare KY 1,086 lines, Humana Medicare
   Advantage 345, UnitedHealthcare Medicare Advantage 45, Anthem Medicare Advantage 39). CPT says not to report them
   together. Whether Medicare's correct coding edits will catch up was not checked, but these payments could be taken back
   in an audit. A coding advisor should decide whether to keep billing 77600 with surface radiation therapy at all.
2. **77436 billed as often as 77437 in 2026** (at Anthem, 77 and 77 in January, similar every month). 77436 is
   "treatment planning and simulation-aided field setting"; 77437 is "delivery, ... per fraction". Worth checking with the
   CPT guidelines how often 77436 may be reported per course (not researched here).
3. **G2211 to commercial payers that never pay it** produces thousands of $0 lines a year (Anthem alone 5,769 since 2025).
   Not billing it there saves posting work and noise; RemitWatch lists almost none of them as items.
4. The detector counts a G2211 "payment" of $0.01 (Aetna Medicare plans, 39 lines) as paid in `_paid_share`; the auditor
   does not (placeholder payments). Harmless here, but the two disagree on what "paid" means.

## For the product (decision 4)

- Every pair above stays pursued (fold_payer_policy defaults to "pursue"; nothing here changes that). Measured on this
  copy: the 7 payer-policy patterns in `ema:payer_policy` all have decision "pursue" (the default), fold_payer_policy
  folded 0 items, C5 left out 0 EMA-only lines, and the 20 code-change items ($7,150) are all on Work now.
- "Why this payer does not pay this code" can be computed live from the store exactly as above (paid / answered as
  primary since 2025, the denial reasons and remarks of the unpaid lines, modifiers, place of service, codes on the same
  claim), with the rule, verdict and sources read from `payer_code_rules.json` by payer group and code. Some entries carry
  `by_service_date` because the rule changed on 2026-01-01 (77600) or the code ended on 2025-12-31 (G6001).
- "What the note or claim must show" for the appeal packet's checklist is the `what_to_show` text of each entry.
- Payer groups in the data file are the code's own names (codes.py and payer_group_kind); EMA's spelling of the same plan
  is listed in `payer_group_aliases`.

## Sources (each fetched twice on 2026-11-26; title, date and every quote matched both times)

| Source | Date | Link |
|---|---|---|
| AMA, Errata and Technical Corrections – CPT® 2026 | June 2, 2026 (entries posted 12/12/25) | https://www.ama-assn.org/system/files/2026-cpt-corrections-errata.pdf |
| CMS, NCD - Hyperthermia for Treatment of Cancer (110.1) | effective 12/31/1984 | https://www.cms.gov/medicare-coverage-database/view/ncd.aspx?NCDId=66 |
| UnitedHealthcare, Radiation Therapy: Fractionation, Image-Guidance, and Special Services (commercial medical policy 2026T0613L) | effective March 1, 2026 | https://www.uhcprovider.com/content/dam/provider/docs/public/policies/comm-medical-drug/radiation-therapy-fractionation-image-special-services.pdf |
| UnitedHealthcare, Radiation and Oncologic Procedures (Medicare Advantage medical policy MMP077.17) | effective September 1, 2026 | https://www.uhcprovider.com/content/dam/provider/docs/public/policies/medadv-mp/radiation-oncologic-procedures.pdf |
| UnitedHealthcare, New simplified radiation therapy prior authorizations | December 23, 2025 | https://www.uhcprovider.com/en/resource-library/news/2026/simplified-radiation-therapy-pa.html |
| VA, Standardized Episodes of Care - Community Care | last updated September 9, 2026 | https://department.va.gov/vha/community-care/care-coordination/standardized-episodes-of-care/ |
| UnitedHealthcare, Rebundling Policy, Professional (2026R0056A) | 1/1/2026 version; Q&A 13 added 9/1/2024 | https://www.uhcprovider.com/content/dam/provider/docs/public/policies/comm-reimbursement/COMM-Rebundling-Policy.pdf |
| Anthem, Bundled Services and Supplies - Professional (C-08003), Virginia copy | effective 12/01/2025 | https://www.anthembluecross.com/content/dam/digital/docs/provider/commercial/policy/reimb/C-08003VA.pdf |
| Anthem, Bundled Services and Supplies – Section 1 coding grid, Virginia | file dated June 22, 2026 | https://www.anthem.com/content/dam/digital/docs/provider/commercial/general/bundled_services_section1_va.pdf |
| CMS, MLN Matters MM13473 (G2211) | revised April 29, 2025 | https://www.cms.gov/files/document/mm13473-how-use-office-and-outpatient-evaluation-and-management-visit-complexity-add-code-G2211.pdf |
| CMS, Podiatry Care (MLN compliance tips) | last modified 02/11/2026 | https://www.cms.gov/training-education/medicare-learning-networkr-mln/compliance/medicare-provider-compliance-tips/podiatry-care |
| WPS, Billing and Coding: Foot Care (A56232) | revision effective 10/01/2025 | https://www.cms.gov/medicare-coverage-database/view/article.aspx?articleid=56232 |
| eCFR, 42 CFR 455.410 | text as of 2026-11-20 | https://www.ecfr.gov/api/versioner/v1/full/2026-11-20/title-42.xml?part=455&section=455.410 |
| X12, Claim Adjustment Reason Codes | current list | https://x12.org/codes/claim-adjustment-reason-codes |
| X12, Remittance Advice Remark Codes | current list | https://x12.org/codes/remittance-advice-remark-codes |

Not reachable from this machine: the web archive (for the 2025 versions of the UnitedHealthcare policy), Aetna's provider
news, and VA's Request for Service page. Not found published: Anthem Kentucky's own copy of C-08003, UMR's and UHC
Integrated Services' G2211 rules, a Kentucky or Indiana Medicaid statement on G2211, and the Kentucky Medicare
contractor's foot care billing article.
