# Clinical Scenario: Benign Excision

## Overview
Benign excisions require proper margin calculation and site-specific coding.

---

## The Critical Formula

```
EXCISED DIAMETER = Lesion + (2 × Margin)
```

---

## Code Selection by Site and Size

### Trunk/Extremities (11400 series)
| Excised Diameter | Code | wRVU |
|------------------|------|------|
| ≤0.5cm | 11400 | 0.88 |
| 0.6-1.0cm | 11401 | 1.25 |
| 1.1-2.0cm | 11402 | 1.41 |
| 2.1-3.0cm | 11403 | 1.79 |
| 3.1-4.0cm | 11404 | 2.06 |
| >4.0cm | 11406 | 3.43 |

### Face (11440 series) - Higher Values
| Excised Diameter | Code | wRVU |
|------------------|------|------|
| ≤0.5cm | 11440 | 1.02 |
| 0.6-1.0cm | 11441 | 1.49 |
| 1.1-2.0cm | 11442 | 1.73 |
| 2.1-3.0cm | 11443 | 2.28 |
| 3.1-4.0cm | 11444 | 3.11 |
| >4.0cm | 11446 | 4.68 |

---

## Calculation Examples

| Lesion | Margin | Excised | Code (trunk) | wRVU |
|--------|--------|---------|--------------|------|
| 4mm | 2mm | 8mm | 11401 | 1.25 |
| 6mm | 2mm | 10mm | 11401 | 1.25 |
| 8mm | 2mm | 12mm | 11402 | 1.41 |
| 12mm | 2mm | 16mm | 11402 | 1.41 |
| 15mm | 3mm | 21mm | 11403 | 1.79 |

---

## Closure Optimization

- **Simple**: Included in excision
- **Intermediate**: Separately billable IF documented (undermining, layered)
- **Complex**: Separately billable IF documented

### Intermediate Triggers
- "Undermining performed"
- "Layered closure"
- "Deep dermal sutures"

---

## Billing Example

**1.2cm cyst of back, 2mm margins, intermediate closure:**

| Component | Code | wRVU |
|-----------|------|------|
| Excision (16mm = 1.6cm) | 11402 | 1.41 |
| Intermediate repair 2.5cm | 12031 | 1.95 |
| **Total** | | **3.36** |

---

## Documentation

> "[Cyst/nevus/lipoma] of [location] measuring [X]cm. Excised with [Y]mm margins. Excised specimen diameter: [calculated]. [Intermediate] closure with [layered technique/undermining]. Sutured length: [X]cm."

---

## "Next Time" Points
- ALWAYS include margins in excised diameter
- Document facial subsite for higher codes
- Intermediate closure if ANY undermining/layers
- Medical necessity: document symptoms (catching, irritation)
