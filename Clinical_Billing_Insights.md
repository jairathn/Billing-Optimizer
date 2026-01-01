# Clinical Billing Insights: Opportunity Taxonomy

This document codifies the patterns for identifying billing optimization opportunities during dermatology visits. It serves as the "teaching layer" for the DermBill AI system.

---

## Section 1: Comorbid Condition Spotting

*"You treated X, but did you look for Y?"*

### Acne Visit Opportunities

| Finding | Look For | Opportunity | Code | wRVU |
|---------|----------|-------------|------|------|
| Acne | Post-inflammatory hyperpigmentation (PIH) | Chemical peel for PIH | 17360 | 0.75 |
| Acne | Inflamed/cystic lesions | IL triamcinolone injection | 11900/11901 | 0.51/0.78 |
| Acne | Closed comedones | Comedone extraction | 10040 | 0.89 |
| Acne | Milia | Milia extraction | 10040 | 0.89 |
| Acne | Keloidal/hypertrophic scarring | IL injection for scars | 11900 | 0.51 |
| Acne | Isotretinoin patient | Monthly monitoring increases MDM | 99214 minimum | 1.92 |

**Documentation triggers for acne upcoding:**
- "PIH noted in areas of prior acne" → supports chemical peel
- "Inflamed cyst on [location]" → supports IL injection
- "Multiple closed comedones" → supports extraction
- "Managing isotretinoin with monthly labs" → supports 99214+

### Psoriasis Visit Opportunities

| Finding | Look For | Opportunity | Code | wRVU |
|---------|----------|-------------|------|------|
| Plaque psoriasis | Nail involvement (pitting, onycholysis) | Document nail disease, higher MDM | 99214+ | 1.92+ |
| Psoriasis | Scalp involvement | Separate anatomic site, may support separate Rx | - | - |
| Psoriasis | Inverse/genital psoriasis | Higher complexity, sensitive site | 99214+ | 1.92+ |
| Psoriasis | Thick resistant plaques | IL injection of thick plaques | 11900/11901 | 0.51/0.78 |
| Psoriasis | Joint pain/stiffness | PsA screening, rheum referral, higher MDM | 99214/99215 | 1.92/2.80 |
| Psoriasis | Biologic management | Drug monitoring = moderate MDM minimum | 99214+ | 1.92+ |
| Any psoriasis | Chronic condition | G2211 add-on | G2211 | 0.33 |

**Documentation triggers:**
- "Nail examination reveals pitting/dystrophy" → higher complexity
- "Patient reports morning stiffness" → PsA screening, higher MDM
- "Discussed risks/benefits of systemic therapy" → 99214 minimum
- "Thick plaque on [location] injected with TAC" → add 11900

### Eczema/Atopic Dermatitis Opportunities

| Finding | Look For | Opportunity | Code | wRVU |
|---------|----------|-------------|------|------|
| Eczema | Secondary impetiginization | Bacterial infection = separate condition | Higher E/M | +MDM |
| Eczema | Eczema herpeticum | Viral superinfection, higher urgency | 99214/99215 | 1.92/2.80 |
| Eczema | Lichenified plaques | IL injection for lichenification | 11900 | 0.51 |
| Eczema | Contact dermatitis component | Patch testing opportunity | 95044 | per allergen |
| Eczema | Dupixent/biologic management | Drug monitoring, moderate MDM | 99214+ | 1.92+ |
| Eczema | Failed topicals, considering systemic | Treatment escalation discussion | 99214 | 1.92 |
| Any eczema | Chronic condition | G2211 add-on | G2211 | 0.33 |

### Seborrheic Dermatitis Opportunities

| Finding | Look For | Opportunity | Code | wRVU |
|---------|----------|-------------|------|------|
| Seborrheic dermatitis | Psoriasis overlap (sebopsoriasis) | Bill for both conditions | Higher E/M | +MDM |
| Seb derm | Thick, resistant plaques | IL injection | 11900 | 0.51 |
| Seb derm | Scalp + face + chest | Multiple sites increases complexity | 99213→99214 | +0.62 |
| Seb derm | Tinea capitis differential | KOH prep, antifungal Rx | 99213+ | - |
| Seb derm | Rosacea overlap | Address both diagnoses | Higher E/M | +MDM |

### Rosacea Opportunities

| Finding | Look For | Opportunity | Code | wRVU |
|---------|----------|-------------|------|------|
| Rosacea | Ocular involvement | Ophthalmology referral, care coordination | 99214 | 1.92 |
| Rosacea | Rhinophyma | Surgical planning discussion, potential destruction | 17000+ | varies |
| Rosacea | Demodex component | Microscopic exam, targeted treatment | 96902 | 0.40 |
| Rosacea | Multiple subtypes | Treating multiple aspects increases MDM | 99214 | 1.92 |
| Rosacea | Topical + oral management | Multiple Rx = low MDM minimum | 99213+ | 1.30+ |

### Hidradenitis Suppurativa (HS) Opportunities

| Finding | Look For | Opportunity | Code | wRVU |
|---------|----------|-------------|------|------|
| HS | Acute abscess | I&D | 10060/10061 | 1.19/2.39 |
| HS | Multiple abscesses | Complicated I&D (10061) | 10061 | 2.39 |
| HS | Sinus tracts | Excision planning, possible excision | 114xx-116xx | varies |
| HS | Keloidal scarring | IL injection of scars | 11900/11901 | 0.51/0.78 |
| HS | Biologic (Humira) management | Drug monitoring, moderate MDM | 99214 | 1.92 |
| HS | Multiple affected sites | Higher MDM complexity | 99214 | 1.92 |
| HS | Chronic condition | G2211 add-on | G2211 | 0.33 |

### Alopecia Opportunities

| Finding | Look For | Opportunity | Code | wRVU |
|---------|----------|-------------|------|------|
| Alopecia areata | Multiple patches | IL injection to patches | 11900/11901 | 0.51/0.78 |
| AA | Nail pitting | Document for complexity | Higher E/M | +MDM |
| AA | Scalp psoriasis component | Separate diagnosis | Higher E/M | +MDM |
| AA | Seborrheic dermatitis | Separate diagnosis | Higher E/M | +MDM |
| Any alopecia | Scalp biopsy indicated | Punch or incisional biopsy | 11104/11106 | 0.81/0.98 |
| Androgenetic alopecia | PRP consideration | Future procedure planning | - | - |
| Alopecia | Hair microscopy | Microscopic exam | 96902 | 0.40 |

### Contact Dermatitis Opportunities

| Finding | Look For | Opportunity | Code | wRVU |
|---------|----------|-------------|------|------|
| Contact dermatitis | Secondary infection | Bacterial superinfection | Higher E/M | +MDM |
| Contact dermatitis | Chronic/recurrent | Patch testing series | 95044 | per allergen |
| Contact dermatitis | Occupational exposure | Work-related increases complexity | 99214 | 1.92 |
| Contact dermatitis | Multiple allergens suspected | Extended patch test panel | 95044 × many | significant |
| Photo-distributed | Photoallergy | Photo patch testing | 95052 | per test |

### Nail Disorder Opportunities

| Finding | Look For | Opportunity | Code | wRVU |
|---------|----------|-------------|------|------|
| Onychomycosis | Multiple dystrophic nails | Debridement | 11720/11721 | 0.31/0.53 |
| Onychomycosis | 6+ nails | Use 11721, not 11720 | 11721 | 0.53 |
| Ingrown nail | Recurrent | Permanent nail ablation | 11750 | 1.54 |
| Nail dystrophy | Psoriatic nails | Document for psoriasis complexity | Higher E/M | +MDM |
| Subungual lesion | Melanonychia | Nail biopsy | 11755 | 1.22 |
| Any nail disorder | Diabetic patient | Medical necessity stronger | G0127 | 0.17 |

### Vitiligo Opportunities

| Finding | Look For | Opportunity | Code | wRVU |
|---------|----------|-------------|------|------|
| Vitiligo | Phototherapy candidate | Initiate NB-UVB | 96900 | per session |
| Vitiligo | Multiple treatment modalities | Higher MDM | 99214 | 1.92 |
| Vitiligo | Chronic management | G2211 add-on | G2211 | 0.33 |

### Wound/Ulcer Opportunities

| Finding | Look For | Opportunity | Code | wRVU |
|---------|----------|-------------|------|------|
| Leg ulcer | Necrotic tissue | Debridement | 11042+ | 0.98+ |
| Venous ulcer | Stasis dermatitis | Treat both conditions | Higher E/M | +MDM |
| Stasis dermatitis | ACD to wound care products | Patch testing | 95044 | per allergen |
| Any wound | Infection | Separate bacterial management | Higher E/M | +MDM |
| Diabetic foot | Dystrophic nails | Nail debridement | 11720/11721 | 0.31/0.53 |
| Wound | Biopsy needed (non-healing) | Punch biopsy | 11104 | 0.81 |

---

## Section 2: Procedure Opportunities During Any Visit

*"While you had the patient there, could you have..."*

### During Full Body Skin Exam

**Systematic anatomic sweep - ask for each area:**

| Body Area | Look For | Opportunity | Code Range |
|-----------|----------|-------------|------------|
| Scalp | AKs, SCCs, seborrheic keratoses | Destruction, biopsy | 17000+, 11102+ |
| Face | AKs, BCCs, sebaceous hyperplasia, rosacea | Destruction, biopsy | 17000+, 11102+, 17110 |
| Ears | AKs, SCCs | Destruction, biopsy | 17000+, 11102+ |
| Neck | Skin tags, AKs | Removal, destruction | 11200+, 17000+ |
| Chest | Seborrheic keratoses, cherry angiomas | Removal if patient wants | 11300+, 17110 |
| Back | Atypical nevi, seborrheic keratoses | Biopsy, removal | 11102+, 11300+ |
| Arms | AKs (extensive sun damage) | Destruction (count all!) | 17000+17003+ |
| Hands | AKs, warts | Destruction | 17000+, 17110 |
| Legs | Stucco keratoses, stasis changes | Destruction if symptomatic | 17110 |
| Feet | Warts, tinea, nail dystrophy | Destruction, debridement | 17110, 11720+ |
| Nails | Any dystrophy | Debridement if indicated | 11720/11721 |
| Intertriginous | Intertrigo, inverse psoriasis, tinea | Diagnosis, treatment | E/M codes |

### For Any Lesion Patient Mentions

| Patient Says | Provider Should Consider | Potential Code |
|--------------|-------------------------|----------------|
| "This mole worries me" | Biopsy for reassurance | 11102-11107 |
| "This growth is catching on clothes" | Shave/remove if symptomatic | 11300+, 11200 |
| "These spots are rough/scaly" | AK destruction | 17000+ |
| "These warts bother me" | Destruction | 17110+ |
| "This cyst hurts" | IL injection or excision | 11900, 114xx |
| "This scar is raised" | IL injection | 11900 |
| "My nails are thick/painful" | Debridement | 11720/11721 |
| "This tag is irritating" | Removal | 11200 |

---

## Section 3: E/M Level Optimization

*"Could this have been a higher-level visit?"*

### MDM Complexity Drivers

| Documentation Element | MDM Impact | Example |
|----------------------|------------|---------|
| 2+ chronic conditions addressed | Moderate minimum | Acne + eczema same visit |
| Prescription requiring monitoring | Low minimum | Any topical steroid Rx |
| Drug with toxicity monitoring | Moderate | Isotretinoin, methotrexate |
| Systemic therapy risk discussion | Moderate | Biologic risks/benefits |
| External records reviewed | Adds to data | Outside path reviewed |
| Test results reviewed | Adds to data | Labs reviewed |
| Care coordination | Adds to complexity | Rheum referral for PsA |
| Independent historian | Adds to data | Parent provides history for child |

### Time-Based Billing Triggers

| Scenario | Consider Time-Based |
|----------|-------------------|
| Extensive counseling documented | If >50% of visit was counseling |
| Complex treatment discussion | Document total time |
| Multiple medication review | Document time spent |
| Difficult social situation | Document counseling time |
| New diagnosis explanation | Document education time |

**If time-based, document:**
- Total time of encounter
- Statement that >50% was counseling/coordination
- Topics of counseling

### G2211 Eligibility (add 0.33 wRVU to EVERY eligible visit)

**Eligible conditions (nearly all chronic derm):**
- Acne (ongoing management)
- Rosacea (chronic)
- Psoriasis (chronic)
- Eczema/atopic dermatitis (chronic)
- Seborrheic dermatitis (chronic)
- Hidradenitis suppurativa (chronic)
- Alopecia (ongoing management)
- Vitiligo (chronic)
- Chronic urticaria
- Any condition requiring ongoing relationship

**Documentation:** "Continued management of chronic [condition]"

---

## Section 4: Biopsy Optimization

*"Was the biopsy coded optimally?"*

### Biopsy Type Selection

| Clinical Scenario | Optimal Technique | Code | wRVU |
|-------------------|-------------------|------|------|
| Superficial lesion, dx known | Shave | 11102 | 0.64 |
| Full-thickness specimen needed | Punch | 11104 | 0.81 |
| Inflammatory dermatosis | Punch (better specimen) | 11104 | 0.81 |
| Large lesion, partial sampling | Incisional | 11106 | 0.98 |
| Panniculitis | Incisional (need deep fat) | 11106 | 0.98 |
| Alopecia workup | Punch (need follicles) | 11104 | 0.81 |
| Nail unit | Nail biopsy | 11755 | 1.22 |

### Multiple Biopsy Optimization

| Scenario | Coding | Total wRVU |
|----------|--------|------------|
| 2 shave biopsies | 11102 + 11103 | 0.64 + 0.37 = 1.01 |
| 3 shave biopsies | 11102 + 11103×2 | 0.64 + 0.74 = 1.38 |
| 2 punch biopsies | 11104 + 11105 | 0.81 + 0.44 = 1.25 |
| 1 shave + 1 punch | 11102 + 11104 | 0.64 + 0.81 = 1.45 |

**Key point:** Each additional biopsy generates add-on wRVU. Count every specimen!

---

## Section 5: Destruction Optimization

*"Were destruction codes maximized?"*

### AK Destruction Counting

| Number of AKs | Coding | Total wRVU |
|---------------|--------|------------|
| 1 | 17000 | 0.61 |
| 5 | 17000 + 17003×4 | 0.61 + 0.36 = 0.97 |
| 10 | 17000 + 17003×9 | 0.61 + 0.81 = 1.42 |
| 14 | 17000 + 17003×13 | 0.61 + 1.17 = 1.78 |
| 15+ | 17004 | 2.59 |

**Key point:** Count EVERY AK. If treating 15+, use 17004 (flat rate, better value).

### Benign Lesion Destruction

| Scenario | Code | wRVU |
|----------|------|------|
| 1-14 warts/molluscum | 17110 | 0.52 |
| 15+ warts/molluscum | 17111 | 0.79 |
| Skin tags (up to 15) | 11200 | 0.80 |
| Skin tags (each addl 10) | +11201 | +0.28 |

---

## Section 6: Repair Optimization

*"Was the closure coded correctly?"*

### Repair Type Determination

| Finding | Repair Type | Code Series |
|---------|-------------|-------------|
| Single layer, no undermining | Simple | 12001-12018 |
| ANY undermining performed | Intermediate | 12031-12057 |
| Buried/deep dermal sutures | Intermediate | 12031-12057 |
| Layered closure | Intermediate | 12031-12057 |
| Wound edge debridement | Complex | 13100-13160 |
| Scar revision | Complex | 13100-13160 |
| Extensive undermining + rearrangement | Complex | 13100-13160 |
| Stents or retention sutures | Complex | 13100-13160 |

### Repair Aggregation Rules

**Same anatomic group + same complexity = AGGREGATE lengths**

| Anatomic Groups for Repair |
|---------------------------|
| Group 1: Scalp/neck/axillae/trunk/extremities |
| Group 2: Face/ears/eyelids/nose/lips/mucous membrane |
| (Some codes split face further into cheeks/chin vs eyelids/nose/ears/lips) |

**Example:**
- Two 1.5cm intermediate repairs on trunk = 3.0cm intermediate trunk = 12032 (2.46 wRVU)
- NOT 12031 × 2 (1.95 × 2 = 3.90 wRVU) ← this would be incorrect

### Measurement Key Points

- Measure SUTURED LENGTH, not defect size
- Include dog ear repairs in total length
- M-plasty conversions add to total length
- Document: "Final sutured wound length: X cm"

---

## Section 7: Excision Optimization

*"Was the excision sized and coded correctly?"*

### THE CRITICAL MEASUREMENT FORMULA

```
Excised Diameter = Lesion Diameter + (2 × Narrowest Margin)
```

**Example calculations:**

| Lesion | Margin | Excised Diameter | Code (trunk, benign) | wRVU |
|--------|--------|------------------|---------------------|------|
| 4mm | 2mm | 4 + 4 = 8mm = 0.8cm | 11401 | 1.25 |
| 6mm | 3mm | 6 + 6 = 12mm = 1.2cm | 11402 | 1.41 |
| 8mm | 4mm | 8 + 8 = 16mm = 1.6cm | 11402 | 1.41 |
| 10mm | 4mm | 10 + 8 = 18mm = 1.8cm | 11402 | 1.41 |
| 12mm | 4mm | 12 + 8 = 20mm = 2.0cm | 11402 | 1.41 |
| 14mm | 4mm | 14 + 8 = 22mm = 2.2cm | 11403 | 1.79 |

**For malignant lesions (typically 4mm margins):**

| Lesion | 4mm Margins | Excised Diameter | Code (trunk, malig) | wRVU |
|--------|-------------|------------------|--------------------| -----|
| 3mm BCC | +8mm | 11mm = 1.1cm | 11602 | 2.21 |
| 5mm BCC | +8mm | 13mm = 1.3cm | 11602 | 2.21 |
| 8mm BCC | +8mm | 16mm = 1.6cm | 11602 | 2.21 |
| 12mm BCC | +8mm | 20mm = 2.0cm | 11602 | 2.21 |
| 14mm BCC | +8mm | 22mm = 2.2cm | 11603 | 2.75 |

### Site Selection Optimization

| Same Excision Size | Trunk Code | wRVU | Face Code | wRVU | Difference |
|--------------------|------------|------|-----------|------|------------|
| ≤0.5cm benign | 11400 | 0.88 | 11440 | 1.02 | +0.14 |
| 0.6-1.0cm benign | 11401 | 1.25 | 11441 | 1.49 | +0.24 |
| 1.1-2.0cm benign | 11402 | 1.41 | 11442 | 1.73 | +0.32 |
| ≤0.5cm malignant | 11600 | 1.59 | 11640 | 1.63 | +0.04 |
| 0.6-1.0cm malig | 11601 | 2.02 | 11641 | 2.12 | +0.10 |
| 1.1-2.0cm malig | 11602 | 2.21 | 11642 | 2.55 | +0.34 |

**Key:** Always document specific anatomic site. Face pays more.

---

## Section 8: Flap/Graft Optimization

*"Was the reconstruction coded correctly?"*

### THE CRITICAL FLAP MEASUREMENT

```
Total Flap Size = Primary Defect + Secondary Defect
```

**This is the #1 source of flap underbilling!**

**Example:**
- Mohs defect on nose: 1.5cm × 1.5cm = 2.25 sq cm (primary)
- Rotation flap creates secondary defect: 1.5cm × 1.5cm = 2.25 sq cm
- **Total: 4.5 sq cm** → 14060 (9.00 wRVU)
- If only measuring primary: would bill 14060 at 2.25 sq cm (same code but underdocumented)

### Graft Optimization

| Graft Type | Measurement | Donor Site |
|------------|-------------|------------|
| FTSG | Recipient site in sq cm | Repair coded separately |
| STSG | Recipient site in sq cm | Included |

**FTSG donor site repair:** Separately billable at full value!

---

## Section 9: Mohs-Specific Optimization

### Block and Stage Counting

| Component | Billing |
|-----------|---------|
| First stage, H/N/HF, up to 5 blocks | 17311 (10.10 wRVU) |
| Each additional block in stage 1 | 17312 (0.95 wRVU) |
| Second stage, up to 5 blocks | 17313 (6.57 wRVU) |
| Each additional block, stages 2+ | 17314 (0.44 wRVU) |

**Example - 3 stage Mohs on nose:**
- Stage 1: 6 blocks = 17311 + 17312 = 10.10 + 0.95 = 11.05
- Stage 2: 4 blocks = 17313 = 6.57
- Stage 3: 3 blocks = 17313 = 6.57
- **Total Mohs: 24.19 wRVU** (before reconstruction)

### Same-Day Reconstruction

Mohs + reconstruction are separately billable. Choose highest-value appropriate reconstruction:

| Reconstruction | Code | wRVU |
|----------------|------|------|
| Intermediate repair face | 12051+ | 2.27+ |
| Complex repair face | 13131+ | 3.64+ |
| Complex repair nose/ear | 13151+ | 4.23+ |
| Flap (nose, 10 sq cm) | 14060 | 9.00 |
| Flap (nose, 10.1-30 sq cm) | 14061 | 11.19 |
| FTSG | 15260+ | varies |

---

## Section 10: Injection Optimization

### Lesion Counting for IL Injection

| Number of Lesions | Code | wRVU |
|-------------------|------|------|
| 1-7 | 11900 | 0.51 |
| 8+ | 11901 | 0.78 |

**Count every injection site!** Multiple keloids, multiple AA patches, multiple cysts = count each.

### Chemotherapy Injection (5-FU, bleomycin)

| Scenario | Code | wRVU |
|----------|------|------|
| IL 5-FU for warts, 1-7 | 96405 | 0.52 |
| IL 5-FU for warts, 8+ | 96406 | 0.80 |
| IL bleomycin for warts | 96405/96406 | 0.52/0.80 |

**Key:** Chemotherapy agents use 96405/96406, not 11900/11901!

---

## Section 11: The "Full Body Exam Sweep" Checklist

**For every comprehensive skin exam, systematically assess:**

### Head/Neck
- [ ] Scalp: AKs? Skin cancers? Seb derm? Psoriasis? Alopecia?
- [ ] Face: AKs? BCCs? SKs? Rosacea? Seb hyperplasia?
- [ ] Ears: AKs? SCCs? (high-risk site!)
- [ ] Nose: AKs? BCCs? (high-risk site!)
- [ ] Lips: Actinic cheilitis? SCC?
- [ ] Neck: Skin tags? AKs? Poikiloderma?

### Trunk
- [ ] Chest: SKs patient wants removed? Nevi to monitor?
- [ ] Back: Atypical nevi? SKs? AKs if sun-damaged?
- [ ] Abdomen: Psoriasis? Intertrigo?

### Extremities
- [ ] Arms: AKs? (count them!) Warts?
- [ ] Hands: AKs? Warts? Nail changes?
- [ ] Legs: Stasis changes? Stucco keratoses?
- [ ] Feet: Warts? Tinea? Onychomycosis?

### Other
- [ ] Nails: Any dystrophy needing debridement?
- [ ] Intertriginous: Intertrigo? Inverse psoriasis? Tinea?
- [ ] Genital area (if indicated): Warts? Psoriasis? Lichen sclerosus?

---

## Section 12: Red Flags - When NOT to Bill

### Audit-Risk Patterns to Avoid

| Pattern | Risk |
|---------|------|
| Modifier -25 on every visit | Overuse audit trigger |
| High E/M with minimal documentation | Upcoding flag |
| G2211 without chronic condition documented | Inappropriate use |
| Multiple destruction codes without counts | Documentation failure |
| Complex repair without complexity factors documented | Insufficient support |
| Flap coded without both defects measured | Incomplete documentation |
| Same high codes every visit | Pattern suggests templating |

### Medical Necessity Requirements

All procedures must have documented medical necessity:
- **Destruction**: Document diagnosis (AK vs benign), symptoms if applicable
- **Biopsies**: Document clinical indication
- **Excisions**: Document diagnosis or clinical concern
- **Chemical peels**: Document medical indication (PIH, actinic damage), not cosmetic
- **Injections**: Document condition being treated

---

## Section 13: Documentation Language Templates

### For E/M Upgrade Support

**Moderate MDM (99214):**
> "Patient has multiple chronic skin conditions including [X] and [Y]. Discussed treatment options including risks and benefits of [medication]. Prescription for [drug] requires monitoring for [side effect]. Plan includes [specifics]."

**High MDM (99215):**
> "Patient presents with [severe condition] posing threat to [bodily function]. Discussed treatment options including [high-risk therapy]. Risks of [specific serious risk] discussed. Considered hospitalization for [indication]. Decision made to [plan]."

### For G2211 Support
> "Continued management of chronic [psoriasis/eczema/acne/etc]. This visit represents ongoing care within established patient-physician relationship."

### For Procedure Support

**IL Injection:**
> "Intralesional triamcinolone [X] mg/mL injected into [number] lesions at [locations]. Total volume [X] mL."

**Intermediate Repair:**
> "Wound closed in layers. Deep dermal sutures placed with [suture type]. Wound edges undermined to reduce tension. Epidermis closed with [suture type]. Final sutured wound length: [X] cm."

**Complex Repair:**
> "Wound edges required debridement of devitalized tissue. Extensive undermining performed with tissue advancement. Retention sutures placed due to [tension/other indication]. Final sutured wound length: [X] cm."

**Flap:**
> "Primary defect measured [X × Y] cm = [Z] sq cm. Adjacent tissue transfer performed using [rotation/advancement/transposition] flap. Secondary defect measured [A × B] cm = [C] sq cm. Total defect for flap: [Z + C] sq cm."

**Excision:**
> "[Benign/Malignant] lesion of [location] measuring [X] cm excised with [Y] mm margins. Excised specimen diameter: [X + 2Y] cm. [Closure type] performed."
