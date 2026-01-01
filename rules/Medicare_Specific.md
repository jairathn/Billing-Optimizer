# Medicare-Specific Billing Rules for Dermatology

## Overview

Medicare has specific rules and coverage policies that differ from commercial payers. This document outlines key Medicare considerations for dermatology billing.

---

## G2211 - Visit Complexity Add-On

### What It Is
An add-on code for outpatient E/M visits (99202-99215) when the visit is part of ongoing care for a chronic condition.

### wRVU
0.33 per visit

### Eligibility Requirements

1. **Outpatient E/M visit** (99202-99215)
2. **Established relationship** with ongoing care
3. **Chronic condition** requiring management over time

### Dermatology Conditions That Qualify

- Psoriasis
- Atopic dermatitis/eczema
- Rosacea
- Acne (ongoing management)
- Hidradenitis suppurativa
- Alopecia (chronic management)
- Chronic urticaria
- Vitiligo
- Seborrheic dermatitis (chronic)
- Skin cancer surveillance
- Any condition requiring ongoing longitudinal care

### Documentation

> "Continued management of chronic [psoriasis/eczema/etc]. This visit represents ongoing care within established patient-physician relationship."

### What Doesn't Qualify

- One-time consultations
- Procedures only (no E/M)
- Acute self-limited conditions
- New patients (typically) unless established for ongoing care

### Billing

- Bill G2211 on same claim as E/M
- No modifier required
- Cannot bill with certain specialty consultations

---

## Global Periods

### Definitions

| Period | Meaning |
|--------|---------|
| 000 | No post-op care, E/M billable next day |
| 010 | 10-day global (minor procedures) |
| 090 | 90-day global (major procedures) |
| XXX | Concept doesn't apply |
| YYY | Carrier determines |

### Common Dermatology Global Periods

| Procedure Type | Typical Global |
|----------------|----------------|
| E/M | 000 |
| Biopsy | 000 |
| Destruction | 010 |
| Simple excision | 010 |
| Excision with intermediate repair | 010 |
| Complex excision | 090 |
| Flaps | 090 |
| Grafts | 090 |
| Mohs surgery | 010 (variable) |

### During Global Period

- **Related visits:** Included in global payment
- **Unrelated visits:** Billable with -24 modifier
- **Complications:** -78 (return to OR)
- **Staged procedures:** -58 (planned)
- **Unrelated procedures:** -79

---

## Medicare Advantage vs Traditional Medicare

### Traditional Medicare (FFS)

- Follows Medicare fee schedule exactly
- NCCI edits apply strictly
- G2211 available

### Medicare Advantage (MA)

- May have additional requirements
- Prior authorization may be needed
- May have different covered benefits
- Policies vary by plan

**Best Practice:** Verify coverage for MA patients

---

## ABN (Advance Beneficiary Notice)

### When Required

- Service may not be covered
- Medical necessity uncertain
- Cosmetic component possible

### Dermatology Scenarios for ABN

| Situation | ABN Needed? |
|-----------|-------------|
| Benign lesion removal (symptomatic) | Maybe - if coverage uncertain |
| Cosmetic procedure | YES |
| Destruction of SKs (patient wants removal) | Maybe - document medical necessity |
| Chemical peel | Likely - if cosmetic component |
| Dermabrasion | Depends on indication |

### ABN Elements

1. Describe service
2. Explain why Medicare may deny
3. Estimated cost
4. Patient options (proceed, don't proceed)
5. Patient signature

---

## LCD/NCD Coverage

### Local Coverage Determinations (LCDs)

Medicare Administrative Contractors (MACs) issue LCDs for specific services. Check your MAC for:

- Phototherapy coverage requirements
- Mohs surgery indications
- Destruction policies
- Repair/reconstruction coverage

### National Coverage Determinations (NCDs)

Apply nationwide. Key NCDs for dermatology:

- **Cosmetic procedures** - generally not covered
- **Photodynamic therapy** - specific indications
- **Dermabrasion** - limited coverage

---

## Medicare-Specific Codes

### G Codes

| Code | Description | wRVU |
|------|-------------|------|
| G2211 | Visit complexity add-on | 0.33 |
| G0127 | Trimming dystrophic nails | 0.17 |

### G0127 - Nail Care

**Coverage requirements:**
- Patient has systemic condition (DM, PVD)
- Nail care required by provider
- Self-care would risk injury

**Documentation:**
> "Nail trimming in patient with diabetes mellitus and peripheral neuropathy. Professional care required due to risk of injury with self-care."

---

## Modifier Usage for Medicare

### -25 Modifier

Medicare scrutinizes -25 usage carefully.

**Requirements:**
- E/M must be separately identifiable
- Documentation must support significant separate work
- Not just pre-procedure assessment

### -59 vs X Modifiers

**Medicare preference:** X modifiers (XE, XS, XP, XU) when applicable

| Modifier | Meaning |
|----------|---------|
| XE | Separate encounter |
| XS | Separate structure |
| XP | Separate practitioner |
| XU | Unusual non-overlapping |

### Bilateral Procedures

**Medicare method:** Bill two lines
- Line 1: Procedure-RT
- Line 2: Procedure-LT

(Not single line with -50)

---

## Multiple Procedure Payment Reduction (MPPR)

### What It Is

Medicare reduces payment for the second and subsequent procedures performed same day by same provider.

### Reduction

- 100% of highest procedure
- 50% of subsequent procedures (typically)

### Impact

When multiple procedures billed:
1. Highest-paying procedure paid at 100%
2. Subsequent procedures reduced

### Strategy

- Bill all appropriate procedures
- Let Medicare apply reductions
- Documentation supports each procedure

---

## Incident-To Billing

### Requirements

1. Physician performed initial service
2. Physician is present in office suite
3. Service is integral part of treatment plan
4. NPP is employed by physician/entity

### Common Dermatology Scenario

- Physician sees patient initially
- PA/NP provides follow-up under physician supervision
- Billed under physician NPI at physician rate

### Caution

- Must meet all requirements
- Physician must be immediately available
- Cannot be used for new patients or new problems

---

## Telehealth for Medicare

### Coverage

Medicare covers some telehealth services with restrictions:
- Geographic limitations (though relaxed during PHE)
- Originating site requirements
- Specific eligible services

### Dermatology Telehealth

- E/M services covered
- Certain consultations
- Check current CMS guidelines for eligible services

---

## Prior Authorization (PA)

### Traditional Medicare

Generally no PA required for standard dermatology services.

### Medicare Advantage

May require PA for:
- Mohs surgery
- Biologic medications
- Phototherapy
- Some surgical procedures

**Best Practice:** Verify PA requirements for MA patients

---

## Quick Reference

### Medicare-Specific Optimizations

| Opportunity | Action |
|-------------|--------|
| Chronic condition visit | Add G2211 (+0.33 wRVU) |
| Diabetic nail care | Bill G0127 with documentation |
| Visit during global | Check if -24/-78/-79 applies |
| Multiple procedures | Bill all; let MPPR apply |
| Bilateral procedure | Bill RT and LT separately |

### Documentation Essentials

- Medical necessity for all procedures
- Chronic condition documentation for G2211
- Systemic condition documentation for nail care
- Measurements for all excisions/repairs
- Lesion counts for destructions
