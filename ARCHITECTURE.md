# DermBill AI: Architecture Document

## Overview

DermBill AI is an LLM-powered billing optimization system for dermatology practices. It analyzes clinical notes and provides three tiers of recommendations:

1. **Current Maximum**: What can be billed from the note as written
2. **Documentation Enhancement**: What language changes would increase billing
3. **Future Opportunities**: What clinical actions could have generated additional revenue

This document defines the architecture, file structure, and LLM interaction patterns for the system.

---

## Repository Structure

```
/billing-corpus
│
├── ARCHITECTURE.md                    # This document
├── CPT_Master_Reference.xlsx          # Comprehensive CPT/HCPCS database
├── Clinical_Billing_Insights.md       # Opportunity taxonomy and teaching patterns
│
├── /rules                             # Billing rules and requirements
│   ├── Modifiers.md                   # Complete modifier reference
│   ├── Measurement_Rules.md           # Sizing rules for all procedure types
│   ├── Repair_Aggregation.md          # Repair length combination rules
│   ├── NCCI_Edits.md                  # Key bundling rules and unbundling
│   ├── Medical_Necessity.md           # Documentation requirements by procedure
│   └── Medicare_Specific.md           # Medicare-specific rules and limitations
│
├── /scenarios                         # Clinical scenario playbooks
│   ├── Scenario_Acne.md
│   ├── Scenario_Psoriasis.md
│   ├── Scenario_Eczema.md
│   ├── Scenario_Seborrheic_Dermatitis.md
│   ├── Scenario_Rosacea.md
│   ├── Scenario_Contact_Dermatitis.md
│   ├── Scenario_HS.md
│   ├── Scenario_Alopecia.md
│   ├── Scenario_Vitiligo.md
│   ├── Scenario_Skin_Check.md
│   ├── Scenario_Skin_Cancer_Surveillance.md
│   ├── Scenario_AK_Treatment.md
│   ├── Scenario_Wart_Treatment.md
│   ├── Scenario_Nail_Disorder.md
│   ├── Scenario_Wound_Care.md
│   ├── Scenario_Biopsy_Diagnostic.md
│   ├── Scenario_Excision_Benign.md
│   ├── Scenario_Excision_Malignant.md
│   ├── Scenario_Mohs_Surgery.md
│   ├── Scenario_Patch_Testing.md
│   ├── Scenario_Phototherapy.md
│   ├── Scenario_Pediatric.md
│   ├── Scenario_Pruritus.md
│   ├── Scenario_Ulcer_Wound.md
│   └── Scenario_Injection_Visit.md
│
├── /documentation                     # Documentation support
│   ├── Documentation_Templates.md     # Billable language snippets
│   ├── Documentation_Red_Flags.md     # Audit triggers to avoid
│   └── Audit_Defense.md               # Supporting documentation strategies
│
└── /reference                         # Supplementary reference materials
    ├── Anatomic_Groupings.md          # Body area classifications for repairs
    ├── ICD10_Mapping.md               # Common diagnosis code pairings
    └── wRVU_Quick_Reference.md        # High-yield code comparisons
```

---

## Core Data Files

### CPT_Master_Reference.xlsx

The central database containing all billable codes. Structure:

**Sheet 1: CPT_Codes**
| Column | Description |
|--------|-------------|
| Code | CPT or HCPCS code |
| Category | Functional grouping (E/M, Biopsy, Destruction, etc.) |
| Subcategory | Specific type within category |
| Official_Description | CMS short descriptor |
| Detailed_Explanation | Plain-English explanation of what the code covers |
| Anatomic_Site | Body location if site-specific (Face, Trunk, etc.) |
| Size_Range | Size parameters if applicable |
| Documentation_Requirements | Mandatory elements for audit survival |
| Measurement_Method | How to measure (if applicable) |
| Common_Underbilling_Errors | Typical mistakes that leave money on table |
| Optimization_Notes | Legitimate strategies to capture full value |
| Related_Codes | Add-ons, alternatives, commonly paired codes |
| Modifier_Notes | Common modifiers used with this code |
| NCCI_Considerations | Key bundling rules |
| Medicare_Notes | Medicare-specific considerations |
| wRVU | Work relative value units (2026) |
| Global_Period | 0, 10, 90, XXX |
| Professional_Component | Yes/No/-26 applicable |
| Add_On_Code | Yes/No |
| Primary_Code_Required | For add-ons, which primary codes apply |

**Sheet 2: Modifiers**
| Column | Description |
|--------|-------------|
| Modifier | Two-digit modifier code |
| Name | Official name |
| Definition | CMS definition |
| When_To_Use | Legitimate use cases |
| When_NOT_To_Use | Audit red flags |
| Derm_Examples | Dermatology-specific scenarios |
| Revenue_Impact | Effect on reimbursement |
| Audit_Risk | Low/Medium/High |

**Sheet 3: Category_Index**
| Column | Description |
|--------|-------------|
| Category | Category name |
| Description | What's included |
| Code_Range | Typical code ranges |
| Key_Optimization_Points | Top opportunities in this category |

---

## LLM Processing Pipeline

### Stage 1: Note Ingestion and Entity Extraction

**Input**: Raw clinical note (any format)

**Process**:
```
1. Parse note into structured segments:
   - Chief complaint / HPI
   - History (PMH, medications, allergies)
   - Physical exam findings
   - Assessment / Diagnoses
   - Plan / Procedures performed
   - Time documentation (if present)

2. Extract entities:
   - Conditions diagnosed (map to ICD-10)
   - Procedures performed
   - Anatomic sites
   - Lesion counts and sizes
   - Medications prescribed/administered
   - Time spent (if documented)

3. Flag ambiguities for clarification
```

**Output**: Structured entity list with confidence scores

### Stage 2: Current Billing Analysis

**Input**: Extracted entities from Stage 1

**Process**:
```
1. For each procedure identified:
   a. Query CPT_Master_Reference.xlsx for matching codes
   b. Verify documentation requirements are met
   c. Check anatomic site coding (use highest-paying appropriate site)
   d. Verify size/count documentation
   e. Apply measurement rules from Measurement_Rules.md

2. For E/M service:
   a. Assess MDM complexity based on documented elements
   b. Check if time-based billing would be higher
   c. Evaluate G2211 applicability

3. Apply modifier logic from Modifiers.md:
   a. Determine if -25 is needed and supportable
   b. Check for multiple procedure situations (-59, -XE/XS/XP/XU)
   c. Identify bilateral procedures (-50)
   d. Check for same-day staged procedures (-58, -78, -79)

4. Run NCCI edit check from NCCI_Edits.md:
   a. Identify bundled code pairs
   b. Determine if unbundling modifiers apply
   c. Flag unsupportable combinations

5. Aggregate repairs per Repair_Aggregation.md:
   a. Group by complexity level
   b. Group by anatomic classification
   c. Sum lengths within groups
   d. Select optimal code for aggregated length
```

**Output**: 
- List of billable codes with modifiers
- Total wRVU calculation
- Documentation gaps that prevent billing

**Documents Used**:
- `CPT_Master_Reference.xlsx` (primary lookup)
- `rules/Modifiers.md`
- `rules/Measurement_Rules.md`
- `rules/Repair_Aggregation.md`
- `rules/NCCI_Edits.md`

### Stage 3: Documentation Enhancement

**Input**: Stage 2 output + original note

**Process**:
```
1. For each documentation gap identified:
   a. Generate specific language to add
   b. Reference Documentation_Templates.md for phrasing
   c. Calculate wRVU delta if gap is closed

2. For each procedure that could be upcoded with better documentation:
   a. Identify what's missing (size, margins, complexity indicators)
   b. Provide specific addendum language
   c. Calculate wRVU improvement

3. For E/M level optimization:
   a. Identify MDM elements that could be better documented
   b. Suggest language additions
   c. If time-based billing is higher, prompt for time documentation

4. Check for complexity indicators not documented:
   a. Layered closure → intermediate repair
   b. Undermining → intermediate repair
   c. Wound contamination → complex repair
   d. Scar revision elements → complex repair
```

**Output**:
- Specific text additions/modifications
- "Before" vs "After" wRVU comparison
- Priority ranking of changes by wRVU impact

**Documents Used**:
- `documentation/Documentation_Templates.md`
- `rules/Medical_Necessity.md`
- `CPT_Master_Reference.xlsx` (Documentation_Requirements column)

### Stage 4: Future Opportunity Analysis

**Input**: Extracted entities + scenario matching

**Process**:
```
1. Match primary diagnosis to scenario file:
   a. Load relevant Scenario_[X].md
   b. Extract "Look For" checklist
   c. Extract "Procedure Opportunities" list

2. Cross-reference Clinical_Billing_Insights.md:
   a. Match primary condition to comorbidity matrix
   b. Generate "Did you look for...?" prompts
   c. Calculate potential wRVU for each opportunity

3. For full body skin exams, run complete anatomic sweep:
   a. Check each body area against opportunity list
   b. Flag undocumented areas
   c. Generate site-specific prompts

4. Evaluate procedure alternatives:
   a. "Could this shave have been a punch for better cosmesis AND higher reimbursement?"
   b. "Was destruction vs excision considered?"
   c. "Could IL injection have been added?"

5. Assess visit-level opportunities:
   a. Could this have been a higher E/M level?
   b. Were there teaching/counseling opportunities?
   c. Was care coordination documented?

6. Generate "Next Time" summary:
   a. Specific clinical actions to consider
   b. wRVU value of each opportunity
   c. Documentation requirements for each
```

**Output**:
- "Next Time" opportunity list with wRVU values
- Clinical prompts ("Did you check for nail involvement?")
- Procedure suggestions ("Consider IL injection for resistant plaques")
- E/M optimization tips

**Documents Used**:
- `Clinical_Billing_Insights.md` (primary)
- `scenarios/Scenario_[Matched].md`
- `CPT_Master_Reference.xlsx`

---

## Document Retrieval Strategy

### Retrieval Priority by Query Type

**For procedure coding questions**:
1. `CPT_Master_Reference.xlsx` → Code lookup
2. `rules/Measurement_Rules.md` → Sizing
3. `rules/Modifiers.md` → Modifier selection
4. `rules/NCCI_Edits.md` → Bundling check

**For E/M coding questions**:
1. `CPT_Master_Reference.xlsx` → E/M codes section
2. `rules/Medical_Necessity.md` → MDM requirements
3. `rules/Modifiers.md` → -25 guidance

**For clinical scenario analysis**:
1. `scenarios/Scenario_[X].md` → Primary playbook
2. `Clinical_Billing_Insights.md` → Comorbidity matrix
3. `CPT_Master_Reference.xlsx` → Code details

**For documentation help**:
1. `documentation/Documentation_Templates.md` → Language
2. `documentation/Documentation_Red_Flags.md` → What to avoid
3. `CPT_Master_Reference.xlsx` → Requirements

### Context Window Management

Given LLM context limitations, use selective retrieval:

1. **Always load**: 
   - Relevant category from `CPT_Master_Reference.xlsx`
   - `rules/Modifiers.md` (core section only)

2. **Load based on note content**:
   - Matched `scenarios/Scenario_[X].md` file(s)
   - Relevant sections of `Clinical_Billing_Insights.md`
   - `rules/Measurement_Rules.md` if procedures present

3. **Load on demand**:
   - `documentation/Documentation_Templates.md` for Stage 3
   - `rules/NCCI_Edits.md` if multiple procedures

---

## Output Format Specification

### Tier 1: Current Maximum Billing

```
## Current Billable Services

| Code | Modifier | Description | wRVU | Documentation Status |
|------|----------|-------------|------|---------------------|
| 99214 | -25 | E/M Established, Moderate | 1.92 | ✓ Supported |
| 11102 | | Tangential bx, trunk | 0.64 | ✓ Supported |
| 17000 | | Destruct premal 1st | 0.61 | ⚠ Size not documented |

**Total Current wRVU: 3.17**

### Documentation Gaps
- AK destruction: Size/number not documented (required for 17003/17004 add-ons)
```

### Tier 2: Documentation Enhancement

```
## Documentation Improvements

### High Impact Changes

**1. Add lesion count for AK destruction**
- Current: 17000 (0.61 wRVU)
- Add to note: "5 actinic keratoses destroyed on bilateral dorsal hands"
- Enhanced: 17000 + 17003 x4 (0.61 + 0.36 = 0.97 wRVU)
- **Delta: +0.36 wRVU**

**2. Document undermining for closure**
- Current: 12001 Simple repair (0.82 wRVU)
- Add to note: "Wound edges undermined to reduce tension; layered closure performed"
- Enhanced: 12031 Intermediate repair (1.95 wRVU)
- **Delta: +1.13 wRVU**

### Suggested Addendum Language
> "Addendum: 5 actinic keratoses were treated with cryotherapy on bilateral dorsal hands. 
> The biopsy site closure required undermining of wound edges with layered closure 
> (deep dermal and epidermal sutures)."

**Total Enhanced wRVU: 4.66 (+1.49)**
```

### Tier 3: Future Opportunities

```
## Opportunities for Next Time

### Condition-Related Opportunities

**1. Psoriasis — Did you assess for nail involvement?**
- Finding: Psoriasis documented on elbows/knees
- Opportunity: Nail pitting/dystrophy increases MDM complexity
- If nail involvement present and treated: +11720 (0.31 wRVU)
- Documentation: "Nail examination reveals pitting consistent with psoriatic nail disease"

**2. Psoriasis — Did you screen for joint symptoms?**
- Opportunity: PsA screening increases MDM, may support 99215
- If positive: Rheumatology referral adds care coordination
- Potential E/M upgrade: 99214 → 99215 (+0.88 wRVU)

### Procedure Opportunities

**3. Seborrheic keratoses noted — Patient bothered?**
- Finding: "Multiple SKs on back" documented but not treated
- Opportunity: If patient desires removal → 17110 (0.52 wRVU)
- Ask: "Are any of these growths bothering you?"

**4. Biopsy technique — Was punch considered?**
- Performed: Shave biopsy of trunk lesion (11102, 0.64 wRVU)
- Alternative: Punch biopsy (11104, 0.81 wRVU) — better for deep lesions
- Consider: If lesion depth uncertain, punch provides better specimen AND higher wRVU
- **Potential delta: +0.17 wRVU**

### Visit-Level Opportunities

**5. G2211 Complex Visit Add-on**
- This patient has chronic psoriasis requiring ongoing management
- G2211 applicable: +0.33 wRVU per visit
- Documentation: "Continued management of chronic psoriasis"

**Total Potential Additional wRVU: 2.01**
```

---

## Algorithmic Components

The following logic can be implemented algorithmically (not requiring LLM inference) to reduce token usage and improve speed:

### 1. Code Lookup
- Input: Code number → Output: All reference data
- Pure database query against `CPT_Master_Reference.xlsx`

### 2. wRVU Calculation
- Sum wRVUs for code list
- Apply modifier adjustments (e.g., -50 bilateral = 1.5x)

### 3. Repair Aggregation
- Input: List of (length, complexity, anatomic site)
- Group by complexity + anatomic group
- Sum lengths within groups
- Return optimal code for each group

### 4. Anatomic Group Classification
- Input: Body site description
- Output: Anatomic group code for repair aggregation
- Rule-based mapping

### 5. NCCI Edit Check
- Input: List of codes
- Output: Flagged pairs with edit type (0, 1, or 9)
- Database lookup

### 6. Size-to-Code Mapping
- Input: Procedure type + size + anatomic site
- Output: Optimal CPT code
- Decision tree

### LLM-Required Components

These require language model inference:

1. **Entity extraction** from free-text notes
2. **Condition-to-scenario matching** (fuzzy matching)
3. **Documentation gap assessment** (semantic analysis)
4. **Clinical opportunity generation** (reasoning)
5. **Natural language output generation**

---

## Maintenance and Updates

### Annual Updates Required

1. **wRVU values**: Update from CMS Physician Fee Schedule (November release)
2. **NCCI edits**: Quarterly updates from CMS
3. **New codes**: Annual CPT updates (AMA, January)
4. **Medicare rules**: LCD/NCD changes as published

### Suggested Update Process

1. Download new CMS PFS data
2. Run diff against current `CPT_Master_Reference.xlsx`
3. Flag changed wRVUs for review
4. Add new codes with full documentation
5. Archive previous version with date stamp

---

## Compliance Considerations

### Built-in Safeguards

1. **Documentation-first approach**: System only suggests codes supportable by documentation
2. **Medical necessity emphasis**: All suggestions include documentation requirements
3. **Audit risk flagging**: High-risk patterns are called out
4. **No upcoding encouragement**: System suggests finding additional legitimate work, not inflating existing work

### Disclaimer Integration

All outputs should include:
> "These recommendations are for educational purposes and require clinical judgment. 
> All billing must reflect services actually performed and documented. 
> Consult your compliance officer for facility-specific guidance."

---

## Performance Metrics

### System Effectiveness Measures

1. **wRVU capture rate**: (Billed wRVU / Maximum justifiable wRVU)
2. **Documentation completion rate**: % of procedures with complete documentation
3. **Opportunity identification rate**: # of actionable suggestions per note
4. **User acceptance rate**: % of suggestions implemented

### Target Benchmarks

- wRVU capture rate: >95%
- Documentation completion: >98%
- Opportunities identified: 2-4 per complex visit
- Suggestion relevance: >80% user acceptance

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2025-01 | Initial architecture |

---

## Appendix: Quick Reference

### High-Value Code Awareness

| Scenario | Lower-Value Approach | Higher-Value Approach | Delta |
|----------|---------------------|----------------------|-------|
| Trunk biopsy | Shave 11102 (0.64) | Punch 11104 (0.81) | +0.17 |
| Face biopsy | Shave 11102 (0.64) | Shave 11305 (0.78) | +0.14 |
| Simple closure | 12001 (0.82) | 12031 intermediate (1.95) | +1.13 |
| Single AK | 17000 (0.61) | 17000 + 17003s if multiple | +0.09/ea |
| E/M without -25 | No procedure E/M | Add -25 when justified | +E/M |
| Chronic condition | 99214 (1.92) | 99214 + G2211 (2.25) | +0.33 |

### Critical Measurement Reminders

| Procedure | Measurement Rule |
|-----------|------------------|
| Excision | Excised diameter + (2 × narrowest margin) |
| Repair | Final sutured length including dog ears |
| Flap | Primary defect + secondary defect |
| Graft | Square centimeters of recipient site |
| Destruction | Count each lesion separately |
