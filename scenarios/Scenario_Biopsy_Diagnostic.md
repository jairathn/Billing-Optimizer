# Clinical Scenario: Diagnostic Biopsy Visit

## Overview
Biopsy visits require technique selection and counting all specimens.

---

## Biopsy Code Selection

| Technique | First | Add-on | wRVU (1st/add) |
|-----------|-------|--------|----------------|
| Shave/Tangential | 11102 | 11103 | 0.64/0.37 |
| Punch | 11104 | 11105 | 0.81/0.44 |
| Incisional | 11106 | 11107 | 0.98/0.53 |

## Technique Selection Guide

| Clinical Scenario | Best Technique | Rationale |
|-------------------|----------------|-----------|
| Superficial lesion, diagnosis known | Shave | Adequate specimen |
| Inflammatory dermatosis | Punch | Full-thickness needed |
| Deep process suspected | Incisional | Deep fat/subcutis |
| Alopecia workup | Punch | Need follicles |
| Panniculitis | Incisional | Need deep fat |
| Nail unit | 11755 | Specific code |

---

## Optimization Strategy

### Punch vs Shave Decision
- Punch (11104): 0.81 wRVU
- Shave (11102): 0.64 wRVU
- **Delta: +0.17 wRVU** when punch is clinically appropriate

### Multiple Biopsies
| Number | Shave | Punch |
|--------|-------|-------|
| 2 | 11102+11103 = 1.01 | 11104+11105 = 1.25 |
| 3 | 11102+11103×2 = 1.38 | 11104+11105×2 = 1.69 |
| 4 | 11102+11103×3 = 1.75 | 11104+11105×3 = 2.13 |

---

## Billing Examples

### Single Shave Biopsy
- 99213-25 + 11102 = 1.94 wRVU

### Two Punch Biopsies
- 99213-25 + 11104 + 11105 = 2.55 wRVU

### Mixed Techniques
- 99214-25 + 11102 + 11104 = 3.37 wRVU

---

## Documentation

> "[Shave/Punch/Incisional] biopsy of [lesion description] at [location]. Indication: [rule out malignancy/diagnose rash/etc]. [For punch: Xmm punch used. Site closed with suture/left open.] Specimen to pathology."

---

## "Next Time" Points
- Choose punch when full-thickness specimen needed (AND higher wRVU)
- Count every biopsy for add-on codes
- Different techniques can each be primary codes
- Incisional (11106) highest for deep specimens
