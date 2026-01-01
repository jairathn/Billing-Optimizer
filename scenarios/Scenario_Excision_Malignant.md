# Clinical Scenario: Malignant Excision (NMSC)

## Overview

Malignant excisions pay significantly more than benign. Key optimization: margin calculation and site-specific coding.

---

## The Critical Measurement

```
EXCISED DIAMETER = Lesion + (2 × Margin)
```

Standard NMSC margins: 4mm = adds 8mm to lesion size

---

## Code Selection by Size and Site

### Trunk/Extremities (11600 series)

| Excised Diameter | Code | wRVU |
|------------------|------|------|
| ≤0.5cm | 11600 | 1.59 |
| 0.6-1.0cm | 11601 | 2.02 |
| 1.1-2.0cm | 11602 | 2.21 |
| 2.1-3.0cm | 11603 | 2.75 |
| 3.1-4.0cm | 11604 | 3.09 |
| >4.0cm | 11606 | 4.89 |

### Face (11640 series) - Higher values

| Excised Diameter | Code | wRVU |
|------------------|------|------|
| ≤0.5cm | 11640 | 1.63 |
| 0.6-1.0cm | 11641 | 2.12 |
| 1.1-2.0cm | 11642 | 2.55 |
| 2.1-3.0cm | 11643 | 3.33 |
| 3.1-4.0cm | 11644 | 4.23 |
| >4.0cm | 11646 | 6.10 |

---

## Margin Calculation Examples

| Lesion | Margin | Excised | Size Category |
|--------|--------|---------|---------------|
| 3mm BCC | 4mm | 11mm = 1.1cm | 1.1-2.0cm |
| 5mm BCC | 4mm | 13mm = 1.3cm | 1.1-2.0cm |
| 10mm SCC | 4mm | 18mm = 1.8cm | 1.1-2.0cm |
| 15mm SCC | 4mm | 23mm = 2.3cm | 2.1-3.0cm |

---

## Closure Optimization

- Simple closure: Included in excision
- Intermediate: Separately billable with documentation
- Complex: Separately billable with documentation
- Flap: Separately billable

### Intermediate Repair Triggers

- Undermining
- Layered closure
- Deep dermal sutures

Document: "Layered closure with undermining performed."

---

## Billing Example

**6mm BCC of nose, 4mm margins, intermediate closure:**

| Component | Code | wRVU |
|-----------|------|------|
| Excision (14mm = 1.4cm, face) | 11642 | 2.55 |
| Intermediate repair face 2.0cm | 12051 | 2.27 |
| **Total** | | **4.82** |

---

## Documentation Template

> "[BCC/SCC] of [location] measuring [X]mm clinically. Excised with [4]mm margins. Excised specimen: [calculated]cm. Path confirmed [diagnosis]. [Intermediate/Complex] repair performed with [technique]. Sutured length: [X]cm."

---

## "Next Time" Points

- ALWAYS include margins in excised diameter calculation
- Document specific facial subsite for higher-paying codes
- Intermediate closure if ANY undermining or layered closure
- Path must confirm malignancy for 116xx codes
