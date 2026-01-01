# Example Cases for DermBill AI Demonstration

## Overview

These are realistic dermatology clinical notes with common documentation patterns. Each case contains deliberate gaps or missed opportunities that DermBill AI should identify.

---

## Case 1: Psoriasis Follow-up (Multiple Missed Opportunities)

**Chief Complaint:** Follow-up psoriasis

**HPI:** 45-year-old male with plaque psoriasis returns for routine follow-up. Has been using clobetasol cream to elbows and knees. Reports moderate improvement but still has thick plaques on elbows. Denies joint pain.

**Exam:**
- Bilateral elbows: thick, silvery plaques, approximately 4x4cm each
- Bilateral knees: thinner plaques, improving
- Scalp: mild scale in frontal hairline
- Nails: not examined

**Assessment:** Plaque psoriasis, partially controlled

**Plan:**
- Continue clobetasol BID to thick plaques
- Add calcipotriene to knees
- Return 3 months

---

**Expected DermBill AI Findings:**

*Current Billing:* 99213 (1.30 wRVU)

*Documentation Enhancements:*
- None identified from current note

*Future Opportunities:*
1. **G2211 not added** - Psoriasis is chronic condition (+0.33 wRVU)
2. **Nails not examined** - 50% of psoriasis patients have nail involvement. If dystrophic, debridement (11721) billable (+0.53 wRVU)
3. **Thick plaques not injected** - IL triamcinolone for resistant elbow plaques (11900) (+0.51 wRVU)
4. **PsA screening incomplete** - Document screening with duration of morning stiffness to support MDM

*Potential missed wRVU:* 1.37

---

## Case 2: Skin Check with AK Treatment (Counting Error)

**Chief Complaint:** Annual skin check

**HPI:** 68-year-old female with history of BCC (excised 2019) presents for annual skin exam. No new concerning lesions noted by patient.

**Exam:** Full body skin examination performed. Sun-damaged skin on face, scalp, and dorsal hands. Multiple AKs identified and treated.

**Procedures:** Cryotherapy to AKs on face and hands.

**Assessment:** 
1. Actinic keratoses
2. History of BCC - stable

**Plan:** Return 1 year for skin check. Sun protection counseling provided.

---

**Expected DermBill AI Findings:**

*Current Billing:* 99213-25, 17000 (1.91 wRVU) - but AK count missing, may be underbilling

*Documentation Enhancements:*
1. **AK count not documented** - Cannot bill add-on codes (17003) without count
   - Add: "12 actinic keratoses treated: 4 on forehead, 3 on right cheek, 2 on left dorsal hand, 3 on right dorsal hand"
   - If 12 AKs: 17000 + 17003×11 = 1.60 wRVU (vs 0.61 if count unknown)
   - Delta: +0.99 wRVU

*Future Opportunities:*
1. **G2211 applicable** - Skin cancer surveillance is chronic management (+0.33 wRVU)
2. **Ears not mentioned** - Common site for AKs, should document examination

*Potential missed wRVU:* 1.32

---

## Case 3: Acne with Cysts (Procedures Not Performed)

**Chief Complaint:** Acne follow-up

**HPI:** 22-year-old female with moderate inflammatory acne. Currently on doxycycline 100mg BID and tretinoin cream. Reports some improvement but has "a few painful bumps" on chin and jawline. Also notes dark spots where previous pimples healed.

**Exam:**
- Face: scattered inflammatory papules and pustules on cheeks
- Chin/jawline: 4 tender, deep nodules
- Post-inflammatory hyperpigmentation on bilateral cheeks
- Comedones on forehead

**Assessment:** Acne vulgaris, moderate, partially improved

**Plan:**
- Continue current regimen
- May consider isotretinoin if not improved in 2 months
- Return 8 weeks

---

**Expected DermBill AI Findings:**

*Current Billing:* 99213 (1.30 wRVU)

*Documentation Enhancements:*
- Consider adding second diagnosis (PIH) to support complexity

*Future Opportunities:*
1. **G2211 not added** - Acne is chronic (+0.33 wRVU)
2. **4 tender nodules not injected** - IL triamcinolone for inflamed cysts (11900) (+0.51 wRVU)
3. **PIH not treated** - Chemical peel (17360) billable for PIH (+0.75 wRVU)
4. **Comedones not extracted** - Acne surgery (10040) billable (+0.89 wRVU)

*Potential missed wRVU:* 2.48

---

## Case 4: Excision with Repair (Measurement and Closure Errors)

**Chief Complaint:** Cyst removal

**HPI:** 52-year-old male with enlarging cyst on upper back, present for 2 years. Occasionally inflamed. Requests removal.

**Procedure Note:**
Epidermal inclusion cyst of upper back. After local anesthesia with 1% lidocaine with epinephrine, elliptical excision performed. Cyst removed intact. Wound closed with sutures. Patient tolerated well.

**Specimen:** Cyst to pathology

**Assessment:** Epidermal inclusion cyst, upper back

**Plan:** Suture removal in 14 days. Call with path results.

---

**Expected DermBill AI Findings:**

*Current Billing:* Unable to determine - missing critical measurements

*Documentation Enhancements:*
1. **Lesion size not documented** - Add: "Cyst measured 1.5cm in diameter"
2. **Margins not documented** - Add: "Excised with 2mm margins. Excised specimen: 1.9cm"
3. **Closure type not specified** - If undermining or layered closure performed, add: "Wound edges undermined. Layered closure with deep dermal 4-0 Vicryl and superficial 4-0 nylon. Sutured length: 3.5cm"

*With proper documentation:*
- Excision 11402 (1.1-2.0cm): 1.41 wRVU
- Intermediate repair 12031 (≤2.5cm) or 12032 (2.6-7.5cm): 1.95-2.46 wRVU
- Total: 3.36-3.87 wRVU

*Without documentation:* May default to 11400 + 12001 = 1.70 wRVU

*Potential missed wRVU:* 1.66-2.17

---

## Case 5: Mohs Surgery (Block Counting and Flap Measurement)

**Chief Complaint:** Mohs surgery for BCC

**HPI:** 71-year-old male with biopsy-proven BCC of left nasal ala.

**Procedure Note:**

*Mohs Excision:*
- Stage 1: Tumor excised with 2mm margins. Positive at 6 o'clock.
- Stage 2: Additional tissue taken at 6 o'clock. Clear margins.

*Reconstruction:*
Defect measured 1.8 x 1.5cm. Bilobed flap performed for closure. 

**Assessment:** BCC left nasal ala, completely excised with Mohs

**Plan:** Wound care instructions given. Return 1 week for suture removal.

---

**Expected DermBill AI Findings:**

*Current Billing:* 17311 + 17313 + 14060 = 25.67 wRVU (but may be underbilling)

*Documentation Enhancements:*
1. **Block counts not documented** 
   - Add: "Stage 1: 4 tissue blocks processed" → If >5 blocks, add 17312
   - Add: "Stage 2: 3 tissue blocks processed"
2. **Flap secondary defect not measured**
   - Add: "Primary defect: 1.8 × 1.5cm = 2.7 sq cm. Secondary defect from bilobed flap: 2.0 × 1.5cm = 3.0 sq cm + 1.5 × 1.0cm = 1.5 sq cm. Total flap defect: 7.2 sq cm"
   - This confirms 14060 (≤10 sq cm) is correct, but documentation supports it

*Future Opportunities:*
- Documentation appears complete for Mohs; ensure block counting is accurate

*Teaching Point:* Bilobed flaps have TWO secondary defects - measure both lobes!

---

## Case 6: Hidradenitis Suppurativa Flare (I&D Complexity)

**Chief Complaint:** HS flare

**HPI:** 34-year-old female with Hurley Stage II HS presents with painful "boil" in left axilla x 3 days. Unable to raise arm due to pain. Has had multiple I&Ds in past.

**Exam:**
- Left axilla: 4cm fluctuant, erythematous, tender abscess
- Multiple sinus tracts and scarring from prior disease
- Right axilla: scarring, no active lesions
- Groin: not examined

**Procedure:**
I&D performed. Large amount of purulent material expressed. Wound packed.

**Assessment:** HS with acute abscess, left axilla

**Plan:** Wound care, packing changes. Continue doxycycline. Follow up 1 week.

---

**Expected DermBill AI Findings:**

*Current Billing:* 99213-25 + 10060 = 2.49 wRVU

*Documentation Enhancements:*
1. **I&D likely qualifies as complicated (10061)**
   - Add: "Large abscess with multiple loculations. Loculations broken up. Deep cavity explored and irrigated. Copious purulent drainage expressed. Wound packed with iodoform gauze."
   - Upgrade 10060 (1.19) → 10061 (2.39) = +1.20 wRVU

2. **E/M may support 99214**
   - Add documentation of Hurley staging, prior treatments, impact on function
   - "Hurley Stage II disease with recurrent abscesses. Unable to perform ADLs due to pain."

*Future Opportunities:*
1. **G2211 not added** - HS is chronic (+0.33 wRVU)
2. **Groin not examined** - Common site; document even if negative
3. **Sinus tracts not addressed** - May be candidates for future excision or deroofing

*Potential missed wRVU:* 1.53+ with 10061 upgrade

---

## Case 7: Eczema with Secondary Infection (Complexity Underestimated)

**Chief Complaint:** Eczema flare

**HPI:** 8-year-old male with history of atopic dermatitis presents with worsening rash x 1 week despite triamcinolone use. Mom notes "yellow crusting" in some areas.

**Exam:**
- Bilateral antecubital fossae: erythematous, lichenified plaques with excoriations
- Left antecubital: honey-colored crusting, weeping
- Bilateral popliteal fossae: similar plaques, no crusting
- Scattered excoriations on trunk

**Assessment:** Atopic dermatitis flare with secondary impetiginization

**Plan:**
- Mupirocin TID to crusted areas
- Continue triamcinolone to non-infected areas
- Bleach baths
- Return 2 weeks

---

**Expected DermBill AI Findings:**

*Current Billing:* 99213 (1.30 wRVU)

*Documentation Enhancements:*
1. **Two diagnoses support higher MDM**
   - Atopic dermatitis (chronic) + impetiginization (acute)
   - Supports 99214 (1.92 wRVU) = +0.62 wRVU

*Future Opportunities:*
1. **G2211 applicable** - Atopic dermatitis is chronic (+0.33 wRVU)
2. **Lichenified plaques could be injected** - If IL steroid appropriate (11900) (+0.51 wRVU)
3. **Parent counseling** - If >50% of visit spent counseling, document time for time-based billing

*Potential missed wRVU:* 0.95-1.46

---

## Case 8: Biopsy Visit (Technique Selection)

**Chief Complaint:** Rash evaluation

**HPI:** 55-year-old female with 3-month history of pruritic rash on lower legs. Has tried OTC hydrocortisone without improvement. No new medications, no similar history.

**Exam:**
- Bilateral lower legs: violaceous, polygonal, flat-topped papules with fine white scale (Wickham's striae)
- No oral involvement
- No nail changes

**Assessment:** Suspected lichen planus

**Plan:** Shave biopsy performed for confirmation. Start triamcinolone 0.1% cream BID. Return for results.

**Procedure:** Shave biopsy left lower leg. Specimen to dermatopathology.

---

**Expected DermBill AI Findings:**

*Current Billing:* 99213-25 + 11102 = 1.94 wRVU

*Documentation Enhancements:*
- Document biopsy indication: "Biopsy to confirm suspected lichen planus"

*Future Opportunities:*
1. **Punch biopsy preferred for inflammatory dermatoses**
   - Lichen planus diagnosis benefits from full-thickness specimen
   - 11104 (0.81) vs 11102 (0.64) = +0.17 wRVU
   - Better diagnostic yield AND higher reimbursement

2. **G2211 if chronic** - If this becomes chronic LP, add G2211 at follow-up

*Teaching Point:* For inflammatory dermatoses where histopathology of the dermal-epidermal junction matters, punch biopsy is clinically preferred AND pays more.

---

## Case 9: Complex Wound Care

**Chief Complaint:** Leg ulcer follow-up

**HPI:** 72-year-old female with chronic venous stasis ulcer right medial lower leg, present for 4 months. Currently using compression and wound care. Ulcer slowly improving.

**Exam:**
- Right medial lower leg: 3 x 2.5cm ulcer with fibrinous base, some granulation tissue at edges
- Surrounding stasis dermatitis with hemosiderin staining
- Mild periwound erythema
- 2+ pitting edema bilateral lower extremities

**Procedure:** Sharp debridement of fibrinous tissue performed.

**Assessment:** 
1. Chronic venous stasis ulcer, right leg
2. Venous insufficiency
3. Stasis dermatitis

**Plan:** Continue compression therapy. Wound care with [dressing]. Return 2 weeks.

---

**Expected DermBill AI Findings:**

*Current Billing:* 99213-25 + 11042 = 2.28 wRVU (but documentation incomplete)

*Documentation Enhancements:*
1. **Wound measurement in sq cm needed**
   - Add: "Wound measures 3.0 × 2.5cm = 7.5 sq cm"
2. **Depth of debridement needed**
   - Add: "Sharp debridement performed to healthy subcutaneous tissue level. Fibrin and nonviable tissue removed."
3. **E/M supports 99214** - Multiple chronic conditions (ulcer + venous insufficiency + stasis dermatitis)
   - Upgrade 99213 → 99214 = +0.62 wRVU

*Future Opportunities:*
1. **G2211** - Chronic wound management (+0.33 wRVU)
2. **Stasis dermatitis** - If treating with topical steroids, document as separate condition being managed

*Potential missed wRVU:* 0.95

---

## Case 10: Alopecia Areata (Injection Opportunity)

**Chief Complaint:** Hair loss

**HPI:** 28-year-old female noticed two "bald spots" on scalp 6 weeks ago. No prior history. Denies stress, recent illness, new medications.

**Exam:**
- Scalp: two well-defined, smooth, circular patches of non-scarring alopecia
  - Occipital: 3cm diameter
  - Parietal: 2cm diameter
- Positive hair pull test at margins
- Exclamation point hairs present
- Nails: no pitting

**Assessment:** Alopecia areata, patchy

**Plan:**
- Discussed treatment options including observation, topical steroids, intralesional steroids
- Patient opts for topical clobetasol solution
- Return 6-8 weeks

---

**Expected DermBill AI Findings:**

*Current Billing:* 99213 (1.30 wRVU)

*Documentation Enhancements:*
- Documentation is appropriate for visit performed

*Future Opportunities:*
1. **IL injection not performed** - 2 patches = 11900 (0.51 wRVU)
   - IL triamcinolone is first-line treatment with best evidence
   - Could have been offered/performed at this visit
2. **G2211 at follow-up** - If condition becomes chronic, add to future visits (+0.33 wRVU)
3. **Nail examination documented** - Good practice, supports thoroughness

*Teaching Point:* IL steroid injection is more effective than topical for AA and is separately billable. Offering this at initial visit captures both better outcomes and appropriate reimbursement.

*Potential missed wRVU:* 0.51

---

## Case 11: Rosacea with Multiple Components

**Chief Complaint:** Rosacea follow-up

**HPI:** 48-year-old female with rosacea on metronidazole cream. Reports facial redness persists. Also mentions "my eyes have been irritated lately" and "dry, gritty feeling."

**Exam:**
- Central face: diffuse erythema, scattered papules, visible telangiectasias
- No rhinophymatous changes
- Eyes: not examined

**Assessment:** Rosacea, papulopustular subtype

**Plan:**
- Add ivermectin cream
- Continue metronidazole
- Return 8 weeks

---

**Expected DermBill AI Findings:**

*Current Billing:* 99213 (1.30 wRVU)

*Documentation Enhancements:*
1. **Ocular symptoms not addressed**
   - Patient reported eye irritation - this suggests ocular rosacea
   - Add eye exam or document referral to ophthalmology
   - Care coordination increases MDM → supports 99214

*Future Opportunities:*
1. **G2211** - Rosacea is chronic (+0.33 wRVU)
2. **Ocular rosacea** - Document as separate component, refer to ophthalmology
   - Multiple subspecialty involvement increases complexity
   - Supports 99214 (1.92) vs 99213 (1.30) = +0.62 wRVU
3. **Demodex evaluation** - If refractory, consider microscopic exam (96902, 0.40 wRVU)

*Potential missed wRVU:* 0.95-1.35

---

## Case 12: Multiple Procedures Same Day

**Chief Complaint:** Several skin concerns

**HPI:** 62-year-old male presents with:
1. Growth on back that "catches on shirts"
2. Changing mole on left arm
3. Rough spots on face and hands

**Exam:**
- Back: 1.5cm pedunculated, skin-colored papule (seborrheic keratosis)
- Left forearm: 7mm brown macule with irregular border
- Face/hands: scattered scaly, rough papules consistent with AKs - approximately 10 lesions total

**Procedures:**
- Shave removal of SK on back for symptomatic relief
- Shave biopsy of left forearm lesion
- Cryotherapy to AKs

**Assessment:**
1. Seborrheic keratosis, symptomatic
2. Atypical nevus vs melanoma - biopsy pending
3. Actinic keratoses

**Plan:** Path results in 1 week. Sun protection counseling.

---

**Expected DermBill AI Findings:**

*Current Billing:* 99214-25 + 11300 + 11102 + 17000 + 17003×9 = Multiple codes but review needed

*Documentation Enhancements:*
1. **SK size not documented precisely**
   - Add measurement for correct shave code (11300 vs 11301)
2. **AK count documented as "approximately 10"**
   - Add: "10 AKs treated: 4 on forehead, 2 on right cheek, 2 on left dorsal hand, 2 on right dorsal hand"
   - Precise count ensures correct billing (17000 + 17003×9 = 1.42 wRVU)
3. **Forearm lesion measurement**
   - Add: "7mm atypical pigmented macule on left forearm"

*Properly Documented:*
- 99214-25: 1.92 wRVU
- 11300 or 11301: 0.68-0.86 wRVU (depending on size)
- 11102: 0.64 wRVU
- 17000 + 17003×9: 1.42 wRVU
- **Total: ~4.66-4.84 wRVU**

*Future Opportunities:*
1. **G2211** - If patient has history of skin cancer or this is surveillance visit (+0.33 wRVU)

---

## Summary: Common Documentation Gaps Demonstrated

| Gap | Cases Demonstrating | Impact |
|-----|---------------------|--------|
| Missing G2211 for chronic conditions | 1, 2, 3, 6, 7, 9, 10, 11 | +0.33 wRVU each |
| AK counts not documented | 2, 12 | Up to +0.99 wRVU |
| Procedures not performed (IL injection) | 1, 3, 10 | +0.51 wRVU each |
| I&D coded as simple vs complicated | 6 | +1.20 wRVU |
| Excision margins/size not documented | 4 | Variable, significant |
| Closure type not specified | 4 | +1.13 wRVU (simple→intermediate) |
| Flap secondary defect not measured | 5 | Potential tier change |
| Mohs blocks not counted | 5 | +0.44-0.95 per extra block |
| Punch vs shave for inflammatory | 8 | +0.17 wRVU |
| Multiple conditions not captured | 7, 11 | +0.62 wRVU (E/M upgrade) |
| Comorbidities not examined | 1, 6, 11 | Variable |

---

## Using These Cases for Demonstration

1. **Input each note** into DermBill AI
2. **Review Current Billing** - verify system captures what IS documented
3. **Review Documentation Enhancements** - verify system identifies gaps
4. **Review Future Opportunities** - verify system suggests missed procedures/optimizations
5. **Calculate potential revenue capture** across a typical day/week

**If a practice sees 20 patients/day with similar patterns:**
- G2211 alone on eligible visits: ~12 visits × 0.33 = 3.96 wRVU/day
- Annualized (220 days): 871 wRVU = ~$50,000+ in captured revenue
