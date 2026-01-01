# DermBill AI: Dermatology Billing Optimization System

## Project Overview

DermBill AI is an LLM-powered billing optimization tool for dermatologists. It analyzes clinical notes and provides actionable recommendations to capture legitimate revenue that is commonly left on the table due to documentation gaps, measurement errors, and missed opportunities.

**This is NOT a fraud tool.** This system helps providers bill correctly for work actually performed and teaches them to recognize billable opportunities during patient encounters.

---

## What to Build

Build a **Python application** with the following components:

### 1. Core Analysis Engine (`dermbill/analyzer.py`)
- Accepts clinical note text as input
- Returns structured JSON with three tiers of recommendations

### 2. Code Lookup Module (`dermbill/codes.py`)
- Loads and queries `CPT_Master_Reference.xlsx`
- Provides wRVU lookups, code details, and optimization notes

### 3. Scenario Matcher (`dermbill/scenarios.py`)
- Matches conditions in note to appropriate scenario files
- Returns relevant optimization opportunities

### 4. Rules Engine (`dermbill/rules.py`)
- Applies modifier logic, measurement rules, NCCI edits
- Validates billing combinations

### 5. CLI Interface (`dermbill/cli.py`)
- Command-line tool for single note analysis
- Accepts file path or stdin

### 6. API Server (`dermbill/api.py`)
- FastAPI or Flask REST API
- POST endpoint accepting note text, returns analysis JSON

---

## The Four-Step Workflow

The system must provide **four distinct outputs** for each note:

### Step 1: Entity Extraction
Parse the clinical note to identify:
- Diagnoses/conditions mentioned
- Procedures performed
- Anatomic sites
- Measurements (sizes, counts, lengths)
- Time documentation
- Medications prescribed/administered

### Step 2: Current Maximum Billing
Analyze what CAN be billed from the note AS WRITTEN:
- List all billable codes with modifiers
- Calculate total wRVU
- Flag any documentation gaps preventing billing
- Show what's currently supportable

**Output format:**
```json
{
  "current_billing": {
    "codes": [
      {"code": "99214", "modifier": "-25", "description": "...", "wRVU": 1.92, "status": "supported"},
      {"code": "11102", "modifier": null, "description": "...", "wRVU": 0.64, "status": "supported"},
      {"code": "17003", "modifier": null, "description": "...", "wRVU": 0.27, "status": "missing_count"}
    ],
    "total_wRVU": 2.56,
    "documentation_gaps": [
      "AK destruction count not specified - cannot bill 17003 add-ons"
    ]
  }
}
```

### Step 3: Documentation Enhancement
Identify what WORDING CHANGES would increase billing:
- Specific language to add to the note
- Before/after wRVU comparison
- Priority ranking by impact

**Output format:**
```json
{
  "documentation_enhancements": [
    {
      "issue": "Closure type not specified",
      "current_code": "12001",
      "current_wRVU": 0.82,
      "suggested_addition": "Add: 'Wound edges undermined. Layered closure with deep dermal 4-0 Vicryl and superficial 5-0 Prolene.'",
      "enhanced_code": "12031",
      "enhanced_wRVU": 1.95,
      "delta_wRVU": 1.13,
      "priority": "high"
    }
  ],
  "suggested_addendum": "Addendum: [complete text to add]",
  "enhanced_total_wRVU": 3.69,
  "improvement": 1.13
}
```

### Step 4: Future Opportunities ("Next Time")
Identify what CLINICAL ACTIONS could have generated additional revenue:
- Comorbidities that could have been identified
- Procedures that could have been performed
- Questions to ask patients
- Examination findings to look for
- wRVU value of each missed opportunity

**This is the teaching layer.** It says things like:
- "Did you check for nail involvement in this psoriasis patient? If present, nail debridement (11721) adds 0.53 wRVU"
- "Patient had acne with PIH. A chemical peel (17360, 0.75 wRVU) was billable if performed"
- "You COULD have looked for seborrheic dermatitis on the scalp - it's common with rosacea"

**Output format:**
```json
{
  "future_opportunities": [
    {
      "category": "comorbidity",
      "finding": "Psoriasis documented",
      "opportunity": "Nail involvement not assessed",
      "action": "Examine nails for pitting, onycholysis, dystrophy",
      "if_positive": {
        "code": "11721",
        "description": "Nail debridement 6+ nails",
        "wRVU": 0.53
      },
      "teaching_point": "50% of psoriasis patients have nail involvement. Always examine nails."
    },
    {
      "category": "procedure",
      "finding": "Thick psoriatic plaques on elbows noted",
      "opportunity": "Intralesional injection not performed",
      "action": "Consider IL triamcinolone for resistant plaques",
      "if_performed": {
        "code": "11900",
        "description": "IL injection ≤7 lesions",
        "wRVU": 0.51
      },
      "teaching_point": "Thick, treatment-resistant plaques respond well to IL steroid and are separately billable."
    },
    {
      "category": "visit_level",
      "finding": "Chronic psoriasis follow-up",
      "opportunity": "G2211 not added",
      "action": "Add G2211 to all chronic condition visits",
      "if_added": {
        "code": "G2211",
        "description": "Visit complexity add-on",
        "wRVU": 0.33
      },
      "teaching_point": "G2211 applies to ANY chronic dermatologic condition. Add to every established visit."
    }
  ],
  "total_potential_additional_wRVU": 1.37
}
```

---

## How to Use the Corpus Documents

### Document Priority by Task

**For code lookups:**
1. `CPT_Master_Reference.xlsx` → Primary database

**For scenario-specific optimization:**
1. Match condition to `scenarios/Scenario_[X].md`
2. Cross-reference `Clinical_Billing_Insights.md`

**For procedure coding:**
1. `rules/Measurement_Rules.md` → Sizing
2. `rules/Modifiers.md` → Modifier selection
3. `rules/NCCI_Edits.md` → Bundling checks
4. `rules/Repair_Aggregation.md` → Repair length aggregation

**For documentation suggestions:**
1. `documentation/Documentation_Templates.md` → Language snippets
2. `rules/Medical_Necessity.md` → Required elements

**For Medicare-specific logic:**
1. `rules/Medicare_Specific.md` → G2211, global periods

### Loading Strategy

On startup, load:
- `CPT_Master_Reference.xlsx` into a searchable data structure
- `Clinical_Billing_Insights.md` into memory for pattern matching

On each note analysis:
- Identify relevant scenario file(s) and load
- Load rule files as needed based on procedures identified

---

## Technical Requirements

### Dependencies
```
pandas
openpyxl
fastapi (or flask)
uvicorn (if fastapi)
pydantic
python-multipart
```

### LLM Integration

The entity extraction and opportunity identification require LLM calls. Structure as:

```python
def analyze_note(note_text: str, corpus: Corpus) -> Analysis:
    # Step 1: Entity extraction (LLM call)
    entities = extract_entities(note_text)
    
    # Step 2: Current billing (algorithmic + LLM validation)
    current = calculate_current_billing(entities, corpus)
    
    # Step 3: Documentation enhancement (LLM reasoning)
    enhancements = identify_enhancements(note_text, entities, current, corpus)
    
    # Step 4: Future opportunities (LLM + scenario matching)
    opportunities = identify_opportunities(entities, corpus)
    
    return Analysis(current, enhancements, opportunities)
```

### API Endpoints

```
POST /analyze
  Body: { "note": "Clinical note text here..." }
  Returns: Complete analysis JSON

GET /codes/{code}
  Returns: Code details, wRVU, optimization notes

GET /scenarios
  Returns: List of available scenarios

GET /scenarios/{scenario_name}
  Returns: Scenario content
```

---

## File Structure to Create

```
dermbill/
├── __init__.py
├── analyzer.py          # Main analysis engine
├── codes.py             # CPT code lookup
├── scenarios.py         # Scenario matching
├── rules.py             # Modifier/NCCI/measurement rules
├── entities.py          # Entity extraction
├── llm.py               # LLM integration (Anthropic API)
├── models.py            # Pydantic models for I/O
├── cli.py               # Command-line interface
├── api.py               # REST API server
└── corpus/              # Symlink or copy of billing-corpus/
    ├── CPT_Master_Reference.xlsx
    ├── Clinical_Billing_Insights.md
    ├── rules/
    ├── scenarios/
    ├── documentation/
    └── reference/
```

---

## Example Usage

### CLI
```bash
# Analyze a note from file
python -m dermbill.cli analyze note.txt

# Analyze from stdin
cat note.txt | python -m dermbill.cli analyze -

# Get code info
python -m dermbill.cli code 11102
```

### API
```bash
# Start server
uvicorn dermbill.api:app --reload

# Analyze note
curl -X POST http://localhost:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{"note": "Patient with psoriasis on elbows and knees..."}'
```

### Python
```python
from dermbill import DermBillAnalyzer

analyzer = DermBillAnalyzer()
result = analyzer.analyze(note_text)

print(f"Current billing: {result.current_billing.total_wRVU} wRVU")
print(f"With enhancements: {result.enhanced_total_wRVU} wRVU")
print(f"Potential additional: {result.future_opportunities.total_potential_wRVU} wRVU")
```

---

## Key Optimization Patterns to Implement

### 1. Excision Margin Calculation
```python
def calculate_excised_diameter(lesion_size_mm: float, margin_mm: float) -> float:
    """Excised diameter = lesion + (2 × margin)"""
    return lesion_size_mm + (2 * margin_mm)
```

### 2. Repair Aggregation
```python
def aggregate_repairs(repairs: List[Repair]) -> List[BillableRepair]:
    """Group repairs by complexity + anatomic group, sum lengths"""
    groups = defaultdict(list)
    for repair in repairs:
        key = (repair.complexity, repair.anatomic_group)
        groups[key].append(repair)
    
    return [
        BillableRepair(
            complexity=complexity,
            anatomic_group=group,
            total_length=sum(r.length for r in repairs),
            code=lookup_repair_code(complexity, group, total_length)
        )
        for (complexity, group), repairs in groups.items()
    ]
```

### 3. Flap Measurement
```python
def calculate_flap_size(primary_defect_cm2: float, secondary_defect_cm2: float) -> float:
    """Total flap = primary + secondary defect"""
    return primary_defect_cm2 + secondary_defect_cm2
```

### 4. AK Destruction Coding
```python
def code_ak_destruction(count: int) -> List[Code]:
    """Return optimal AK destruction codes"""
    if count >= 15:
        return [Code("17004", wRVU=2.59)]
    elif count >= 1:
        codes = [Code("17000", wRVU=0.61)]
        if count > 1:
            codes.append(Code("17003", units=count-1, wRVU=0.09 * (count-1)))
        return codes
    return []
```

### 5. G2211 Eligibility
```python
CHRONIC_CONDITIONS = [
    "psoriasis", "eczema", "atopic dermatitis", "rosacea", "acne",
    "hidradenitis", "alopecia", "vitiligo", "seborrheic dermatitis",
    "chronic urticaria", # ... etc
]

def is_g2211_eligible(diagnoses: List[str]) -> bool:
    """Check if any diagnosis is a chronic condition"""
    return any(
        chronic in diagnosis.lower() 
        for diagnosis in diagnoses 
        for chronic in CHRONIC_CONDITIONS
    )
```

---

## Testing

Create test cases for common scenarios:

```python
# test_analyzer.py

def test_psoriasis_with_nail_involvement():
    note = """
    Follow-up for plaque psoriasis. Current treatment: clobetasol cream.
    Exam: Thick plaques on bilateral elbows and knees. 
    Nail exam reveals pitting on 8 nails. 
    Nail debridement performed.
    Continue current therapy.
    """
    result = analyzer.analyze(note)
    
    # Should capture:
    assert "99214" in result.current_billing.codes  # Multiple conditions
    assert "11721" in result.current_billing.codes  # Nail debridement 6+
    assert "G2211" in result.future_opportunities   # If not already billed

def test_mohs_with_flap():
    note = """
    Mohs for BCC left nasal sidewall.
    Stage 1: 4 blocks, positive at 3 o'clock
    Stage 2: 3 blocks, clear margins
    Final defect: 1.5 x 1.5 cm = 2.25 sq cm
    Advancement flap performed. Secondary defect 2.0 x 1.5 cm = 3.0 sq cm
    Total flap defect: 5.25 sq cm
    """
    result = analyzer.analyze(note)
    
    assert "17311" in result.current_billing.codes  # Mohs stage 1
    assert "17313" in result.current_billing.codes  # Mohs stage 2
    assert "14060" in result.current_billing.codes  # Flap nose ≤10 sq cm
```

---

## Compliance Notice

Include this in all outputs:

> "These recommendations are for educational purposes and require clinical judgment. All billing must reflect services actually performed and documented. This tool identifies optimization opportunities within legitimate coding guidelines. Consult your compliance officer for facility-specific guidance."

---

## Getting Started

1. Read `ARCHITECTURE.md` for detailed system design
2. Load `CPT_Master_Reference.xlsx` and understand the data structure
3. Review `Clinical_Billing_Insights.md` for the opportunity patterns
4. Build the entity extraction first (this is the foundation)
5. Implement code lookup and wRVU calculation
6. Add scenario matching
7. Implement the four-step output workflow
8. Build CLI and API interfaces

---

## Questions?

The `ARCHITECTURE.md` file contains the complete system design including:
- Document retrieval strategy
- LLM processing pipeline
- Output format specifications
- Maintenance and update procedures

Refer to it for detailed implementation guidance.
