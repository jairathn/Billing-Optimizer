# Repair Aggregation Rules

## Fundamental Principle

Repairs of the **same classification** (simple, intermediate, complex) within the **same anatomic group** must be **added together** and reported as a single repair code.

---

## Anatomic Groupings for Repair Codes

### Simple Repair Groups (12001-12021)

| Code Range | Anatomic Group |
|------------|----------------|
| 12001-12007 | Scalp, neck, axillae, external genitalia, trunk, extremities |
| 12011-12018 | Face, ears, eyelids, nose, lips, mucous membranes |
| 12020-12021 | Subcutaneous tissue closure (add-on) |

### Intermediate Repair Groups (12031-12057)

| Code Range | Anatomic Group |
|------------|----------------|
| 12031-12037 | Scalp, axillae, trunk, extremities (not hands/feet) |
| 12041-12047 | Neck, hands, feet, external genitalia |
| 12051-12057 | Face, ears, eyelids, nose, lips, mucous membranes |

### Complex Repair Groups (13100-13160)

| Code Range | Anatomic Group |
|------------|----------------|
| 13100-13102 | Trunk |
| 13120-13122 | Scalp, arms, legs |
| 13131-13133 | Forehead, cheeks, chin, mouth, neck, axillae, genitalia, hands, feet |
| 13151-13153 | Eyelids, nose, ears, lips |

---

## Aggregation Rules

### Rule 1: Same Group + Same Complexity = ADD LENGTHS

**Example:**
- Intermediate repair on trunk: 2.0cm
- Intermediate repair on arm: 1.5cm
- Both are Group 12031-12037
- **Total: 3.5cm → Code 12032 (2.6-7.5cm)**

### Rule 2: Different Groups = SEPARATE CODES

**Example:**
- Intermediate repair on trunk: 2.0cm → 12031
- Intermediate repair on face: 2.0cm → 12051
- **Bill both codes separately (different anatomic groups)**

### Rule 3: Different Complexity = SEPARATE CODES

**Example:**
- Simple repair on arm: 2.0cm → 12001
- Intermediate repair on arm: 2.0cm → 12031
- **Bill both codes separately (different complexity levels)**

### Rule 4: Most Complicated Determines Base, Then Add Lengths

When repairs of different complexities are in the same anatomic group, report each complexity level separately. Do NOT convert all to the highest complexity.

---

## Length Thresholds by Code

### Simple Repairs

| Trunk/Extremities | Face |
|-------------------|------|
| 12001: ≤2.5cm | 12011: ≤2.5cm |
| 12002: 2.6-7.5cm | 12013: 2.6-5.0cm |
| 12004: 7.6-12.5cm | 12014: 5.1-7.5cm |
| 12005: 12.6-20.0cm | 12015: 7.6-12.5cm |
| 12006: 20.1-30.0cm | 12016: 12.6-20.0cm |
| 12007: >30.0cm | 12017: 20.1-30.0cm |
| | 12018: >30.0cm |

### Intermediate Repairs

| Trunk/Extremities | Neck/Hands/Feet | Face |
|-------------------|-----------------|------|
| 12031: ≤2.5cm | 12041: ≤2.5cm | 12051: ≤2.5cm |
| 12032: 2.6-7.5cm | 12042: 2.6-7.5cm | 12052: 2.6-5.0cm |
| 12034: 7.6-12.5cm | 12044: 7.6-12.5cm | 12053: 5.1-7.5cm |
| 12035: 12.6-20.0cm | 12045: 12.6-20.0cm | 12054: 7.6-12.5cm |
| 12036: 20.1-30.0cm | 12046: 20.1-30.0cm | 12055: 12.6-20.0cm |
| 12037: >30.0cm | 12047: >30.0cm | 12056: 20.1-30.0cm |
| | | 12057: >30.0cm |

### Complex Repairs

| Trunk | Scalp/Arms/Legs | Forehead/Cheeks/Etc | Eyelids/Nose/Ears/Lips |
|-------|-----------------|---------------------|------------------------|
| 13100: 1.1-2.5cm | 13120: 1.1-2.5cm | 13131: 1.1-2.5cm | 13151: 1.1-2.5cm |
| 13101: 2.6-7.5cm | 13121: 2.6-7.5cm | 13132: 2.6-7.5cm | 13152: 2.6-7.5cm |
| 13102: +ea 5cm | 13122: +ea 5cm | 13133: +ea 5cm | 13153: +ea 5cm |

**Note:** Complex repairs <1.1cm are coded as intermediate repairs.

---

## Aggregation Examples

### Example 1: Same Group, Same Complexity
**Scenario:** Two wounds on trunk requiring intermediate repair
- Wound A: 2.0cm on back
- Wound B: 1.8cm on abdomen

**Calculation:**
- Both are intermediate, both trunk → aggregate
- Total: 2.0 + 1.8 = 3.8cm
- Code: 12032 (intermediate trunk 2.6-7.5cm)

### Example 2: Different Groups
**Scenario:** Repairs on trunk and face
- Trunk: 2.5cm intermediate
- Face: 2.0cm intermediate

**Calculation:**
- Different anatomic groups → code separately
- Trunk 2.5cm → 12031
- Face 2.0cm → 12051
- Bill both codes

### Example 3: Different Complexities
**Scenario:** Simple and intermediate on same extremity
- Simple closure: 3.0cm
- Intermediate closure: 2.0cm

**Calculation:**
- Different complexities → code separately
- Simple 3.0cm → 12002
- Intermediate 2.0cm → 12031
- Bill both codes

### Example 4: Multiple Same-Group Repairs
**Scenario:** Three wounds on scalp/trunk, all intermediate
- Back: 1.5cm
- Arm: 2.0cm
- Shoulder: 1.0cm

**Calculation:**
- All same group (trunk/extremities intermediate)
- Total: 1.5 + 2.0 + 1.0 = 4.5cm
- Code: 12032 (2.6-7.5cm)

### Example 5: Complex Repair with Add-on
**Scenario:** Large complex repair on trunk
- Total sutured length: 12cm

**Calculation:**
- 13101 covers 2.6-7.5cm (base)
- Remaining: 12 - 7.5 = 4.5cm (rounds to one 5cm unit)
- Code: 13101 + 13102

### Example 6: Mixed Scenario
**Scenario:** Multiple repairs in one surgery
- Face, intermediate: 2.0cm
- Face, intermediate: 1.5cm
- Trunk, simple: 3.0cm
- Trunk, intermediate: 2.0cm

**Calculation:**
- Face intermediate: 2.0 + 1.5 = 3.5cm → 12052
- Trunk simple: 3.0cm → 12002
- Trunk intermediate: 2.0cm → 12031
- Bill: 12052 + 12002 + 12031

---

## Decision Flowchart

```
For each wound requiring repair:
│
├── 1. Determine repair complexity (simple/intermediate/complex)
│
├── 2. Identify anatomic group
│
├── 3. Group all repairs by:
│   └── Same complexity AND same anatomic group
│
├── 4. For each group:
│   └── Sum all repair lengths in that group
│
└── 5. Select code based on total length for each group
```

---

## Special Considerations

### Dog Ear Repairs
- Include in total wound length
- Are part of the primary repair, not separate

### M-Plasty
- Include total sutured length
- The M-plasty extends the wound

### Wounds Converted to Ellipse
- Measure final sutured ellipse length
- Not original wound dimensions

### Debridement Before Repair
- Debridement may be separately billable
- If debridement is incidental to repair, included in repair

### Retention Sutures
- Indicate complex repair
- Not separately billable

---

## Common Aggregation Errors

| Error | Correction |
|-------|------------|
| Billing multiple codes for same group | Aggregate and bill once |
| Adding different complexity levels together | Keep separate |
| Adding different anatomic groups | Keep separate |
| Forgetting dog ears in length | Always include total sutured length |
| Using defect size instead of repair length | Measure final sutured wound |
| Complex repair <1.1cm billed as complex | Use intermediate code instead |

---

## Quick Reference: Same Anatomic Group?

| Site 1 | Site 2 | Same Group? |
|--------|--------|-------------|
| Back | Arm | YES (trunk/extremity) |
| Face | Ear | YES (face group) |
| Back | Face | NO (different groups) |
| Hand | Arm | NO (different groups for intermediate) |
| Scalp | Trunk | YES (for simple/intermediate) |
| Nose | Cheek | DEPENDS on code series |
| Neck | Face | NO (different groups) |

**When in doubt:** Check the specific code descriptions in the CPT codebook for exact anatomic groupings, as they vary between simple, intermediate, and complex series.

---

## Worksheet for Repair Aggregation

```
Repair 1: ____ cm, [Simple/Int/Complex], Site: ______, Group: ______
Repair 2: ____ cm, [Simple/Int/Complex], Site: ______, Group: ______
Repair 3: ____ cm, [Simple/Int/Complex], Site: ______, Group: ______
Repair 4: ____ cm, [Simple/Int/Complex], Site: ______, Group: ______

Aggregation:
Group A (______/______): ____ + ____ = ____ cm → Code: ______
Group B (______/______): ____ + ____ = ____ cm → Code: ______
Group C (______/______): ____ cm → Code: ______
```
