# NCCI Edits for Dermatology

## Overview

The National Correct Coding Initiative (NCCI) edits are pairs of CPT/HCPCS codes that should not generally be reported together. Understanding these edits is essential for correct billing and avoiding claim denials.

---

## NCCI Edit Types

### Column 1/Column 2 Edits

When two codes are bundled:
- **Column 1**: The comprehensive code (can be billed)
- **Column 2**: The component code (bundled into Column 1)

### Modifier Indicators

- **0**: NEVER use a modifier to unbundle
- **1**: Modifier MAY be used if circumstances warrant
- **9**: Not applicable (edit deleted or not active)

---

## Key Dermatology NCCI Bundles

### E/M with Procedures

| Column 1 (Procedure) | Column 2 (E/M) | Modifier | Notes |
|---------------------|----------------|----------|-------|
| Most procedures | 99211-99215 | 1 | Use -25 on E/M if separate service |
| Minor procedures | 99211-99215 | 1 | -25 requires truly separate E/M work |

**Rule:** E/M is bundled into procedures UNLESS the E/M represents separately identifiable work (modifier -25).

**Key Point:** Don't use -25 reflexively. The E/M must represent significant separate work beyond the procedure decision.

### Biopsy Bundles

| Column 1 | Column 2 | Modifier | Explanation |
|----------|----------|----------|-------------|
| 11102 (shave bx) | 12001-12007 (simple repair) | 0 | Simple closure included in biopsy |
| 11104 (punch bx) | 12001-12007 (simple repair) | 0 | Simple closure included in biopsy |
| 11106 (incisional bx) | 12001-12007 (simple repair) | 0 | Simple closure included in biopsy |
| 11102 | 11104 | 1 | Different techniques same lesion = only one |
| 11102 | 11106 | 1 | Different techniques same lesion = only one |
| 114xx (excision) | 11102-11107 (biopsy) | 1 | If same lesion, only excision |

**Key Rules:**
- Biopsy includes simple closure
- Intermediate or complex closure of biopsy site MAY be separately billable with documentation
- If biopsy then excision of same lesion same day, only bill excision
- Different lesions can have biopsy + excision separately

### Excision Bundles

| Column 1 | Column 2 | Modifier | Explanation |
|----------|----------|----------|-------------|
| 114xx/116xx (excision) | 12001-12007 (simple repair) | 0 | Simple closure included |
| 114xx/116xx (excision) | 11102-11107 (biopsy) | 1 | Same lesion = only excision |
| 116xx (malignant exc) | 114xx (benign exc) | 0 | Use one or other based on path |

**Excision includes:**
- Simple closure
- Local anesthesia
- Hemostasis

**Excision does NOT include:**
- Intermediate or complex closure (separately billable with documentation)
- Adjacent tissue transfer (separately billable)
- Skin graft (separately billable)

### Repair Bundles

| Column 1 | Column 2 | Modifier | Explanation |
|----------|----------|----------|-------------|
| Complex repair | Intermediate repair | 0 | Same wound = only complex |
| Complex repair | Simple repair | 0 | Same wound = only complex |
| Intermediate repair | Simple repair | 0 | Same wound = only intermediate |
| Flap (14xxx) | Repair codes | 0 | Repair of donor site included |

**Key Point:** One wound = one repair code. Use the highest complexity that applies.

### Destruction Bundles

| Column 1 | Column 2 | Modifier | Explanation |
|----------|----------|----------|-------------|
| 17000 (AK 1st) | 17003 (AK 2-14) | 0 | 17003 is add-on, always with 17000 |
| 17004 (AK 15+) | 17000-17003 | 0 | Use 17004 alone for 15+ |
| 17110 (benign 1-14) | 17111 (benign 15+) | 0 | Use one or other |
| 17000 (premalignant) | 17110 (benign) | 1 | Different lesions = both billable |

**Key Point:** Don't mix 17000 series with 17004. If 15+ AKs, use 17004 only.

### Flap/Graft Bundles

| Column 1 | Column 2 | Modifier | Explanation |
|----------|----------|----------|-------------|
| 14xxx (flap) | 12001-13160 (repairs) | 0 | Flap includes repair of defects |
| 14xxx (flap) | 14xxx (another flap) | 1 | Multiple flaps different sites = both |
| 15xxx (graft) | 14xxx (flap) | 1 | Different procedures, may both apply |

**FTSG special rule:** Donor site closure IS separately billable (different site).

### Mohs Bundles

| Column 1 | Column 2 | Modifier | Explanation |
|----------|----------|----------|-------------|
| 17311 (Mohs stage 1) | 17312 (addl blocks) | 0 | 17312 is add-on |
| 17313 (Mohs stage 2+) | 17314 (addl blocks) | 0 | 17314 is add-on |
| 17311-17317 | 114xx-116xx (excision) | 0 | Mohs IS the excision |
| 17311-17317 | 11102-11107 (biopsy) | 0 | If same tumor |

**Key Point:** Mohs includes excision and path. Don't also bill excision codes.

### Injection Bundles

| Column 1 | Column 2 | Modifier | Explanation |
|----------|----------|----------|-------------|
| 11900 (IL 1-7) | 11901 (IL 8+) | 0 | Use one or other |
| 96405 (chemo 1-7) | 96406 (chemo 8+) | 0 | Use one or other |
| 11900/11901 | 96405/96406 | 1 | Different drugs = may bill both |

---

## Unbundling with Modifiers

### When Modifier -59 (or X modifiers) Allow Unbundling

**Legitimate reasons:**
- Different anatomic site
- Different lesion/wound
- Different encounter same day
- Truly separate and distinct procedures

**Documentation required:**
- Clear identification of different sites
- Medical necessity for each procedure
- Separate procedure notes if helpful

### When Modifiers DON'T Work (Indicator 0)

These bundles cannot be unbundled:
- Simple repair with biopsy
- Simple repair with excision
- Add-on codes with their primary codes
- Same lesion biopsy + excision

---

## Common Bundling Scenarios

### Scenario 1: Biopsy and Excision Same Lesion

**Question:** Patient has biopsy, returns same day for excision of same lesion.

**Answer:** Bill only the excision. Biopsy is bundled when same lesion excised same day.

**Exception:** If biopsy was diagnostic (different day) and excision is treatment, both may be billable.

### Scenario 2: Biopsy with Intermediate Closure

**Question:** Punch biopsy site required layered closure.

**Answer:** MAY be separately billable IF:
- Documentation supports intermediate closure was necessary
- Typical punch biopsy closure would be simple
- Note describes layered closure explicitly

**Coding:** 11104 + 12031 (with documentation justifying intermediate repair)

### Scenario 3: Multiple Destructions Different Types

**Question:** Treated AKs and warts same day.

**Answer:** Bill both 17000 series (AKs) and 17110/17111 (warts). Different lesion types, different codes.

### Scenario 4: Excision with Flap

**Question:** Excised lesion, closed with advancement flap.

**Answer:** Bill both:
- Excision code (for removal of lesion)
- Flap code (for reconstruction)

The flap code includes closure of both primary and secondary defects, so don't also bill repair codes.

### Scenario 5: Multiple Excisions

**Question:** Two excisions, one on arm, one on face.

**Answer:** Bill both excision codes. Use appropriate modifier (-59 or XS) on second procedure if needed by payer.

### Scenario 6: E/M Plus Multiple Procedures

**Question:** Saw patient for acne (E/M), extracted comedones, and injected a cyst.

**Answer:**
- E/M with -25 (acne management is separate work)
- 10040 (comedone extraction)
- 11900 (IL injection)

All three separately billable with documentation.

---

## Quick Reference: Can I Bill Both?

| Procedure A | Procedure B | Both Billable? |
|-------------|-------------|----------------|
| Biopsy | Simple repair same site | NO |
| Biopsy | Intermediate repair same site | MAYBE with documentation |
| Biopsy | Excision same lesion same day | NO (only excision) |
| Biopsy | Excision different lesion | YES |
| Excision | Simple repair same site | NO |
| Excision | Intermediate/complex same site | YES with documentation |
| Excision | Flap | YES |
| Flap | Repair of flap donor site | NO (included) |
| FTSG | Repair of FTSG donor site | YES |
| AK destruction | Wart destruction | YES (different codes) |
| Mohs | Repair/reconstruction | YES |
| Mohs | Excision same tumor | NO |
| E/M | Any procedure | YES with -25 if separate work |

---

## Avoiding NCCI Denials

### Prevention Strategies

1. **Know the bundles** for your common procedures
2. **Document thoroughly** when unbundling is appropriate
3. **Use correct modifiers** (X modifiers when appropriate)
4. **Don't bill what's included** (simple closure with biopsy, etc.)
5. **Verify different sites/lesions** are clearly documented

### When Claims Are Denied

1. Review the NCCI edit pair
2. Determine if unbundling modifier applies
3. Check documentation supports separate procedures
4. Appeal with documentation if appropriate
5. Consider if billing was actually incorrect

---

## NCCI Resources

- CMS NCCI edit files: Updated quarterly
- Medicare NCCI lookup tool: Available on CMS website
- Many EHRs have built-in NCCI checking

**Note:** NCCI edits are updated quarterly. Verify current edits for specific code pairs.
