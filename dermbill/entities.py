"""
Entity Extraction Module

Extracts structured entities from clinical notes using LLM.
This is the foundation for all subsequent billing analysis.
"""

import re
from typing import Optional

from .models import ExtractedEntities, ExtractedEntity


# Prompt template for entity extraction
ENTITY_EXTRACTION_PROMPT = """You are a medical billing expert analyzing a dermatology clinical note.
Extract all relevant billing entities from the following note.

Return a JSON object with these fields:
- diagnoses: list of conditions/diagnoses mentioned (strings)
- procedures: list of procedures performed (strings, include technique details)
- anatomic_sites: list of body locations mentioned (strings)
- measurements: list of objects with {type, value, unit, context} for any sizes, counts, or lengths
- medications: list of medications prescribed or administered (strings)
- time_documentation: string with any time documentation found, or null

Be thorough - identify ALL:
1. Primary and secondary diagnoses
2. Every procedure mentioned (biopsies, destructions, excisions, injections, repairs, etc.)
3. All anatomic sites/body locations
4. All measurements (lesion sizes, repair lengths, lesion counts, margins)
5. All medications (topicals, injectables, oral medications)
6. Any time documentation (total visit time, counseling time)

Clinical Note:
---
{note_text}
---

Respond with only valid JSON, no markdown formatting."""


def parse_measurements_from_text(text: str) -> list[dict]:
    """
    Parse measurements from text using regex patterns.

    This is a fallback/supplement to LLM extraction.

    Args:
        text: Clinical note text

    Returns:
        List of measurement dicts
    """
    measurements = []

    # Size patterns: X mm, X cm, X x Y mm, etc.
    size_patterns = [
        # X.X cm or X cm
        (r'(\d+\.?\d*)\s*(cm|mm)\s*(?:lesion|mass|nodule|plaque|defect)?',
         lambda m: {"type": "size", "value": float(m.group(1)), "unit": m.group(2)}),

        # X x Y cm (dimensions)
        (r'(\d+\.?\d*)\s*[xX×]\s*(\d+\.?\d*)\s*(cm|mm)',
         lambda m: {"type": "dimensions", "value": f"{m.group(1)} x {m.group(2)}", "unit": m.group(3)}),

        # X mm margins
        (r'(\d+\.?\d*)\s*(mm|cm)\s*margin',
         lambda m: {"type": "margin", "value": float(m.group(1)), "unit": m.group(2)}),

        # sq cm (square centimeters)
        (r'(\d+\.?\d*)\s*(sq\.?\s*cm|cm2|cm²)',
         lambda m: {"type": "area", "value": float(m.group(1)), "unit": "sq cm"}),
    ]

    for pattern, extractor in size_patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            try:
                measurement = extractor(match)
                measurement["context"] = text[max(0, match.start()-20):match.end()+20]
                measurements.append(measurement)
            except (ValueError, IndexError):
                continue

    # Count patterns: X lesions, X AKs, etc.
    count_patterns = [
        (r'(\d+)\s*(?:actinic keratoses|aks?|actinic lesions)',
         lambda m: {"type": "ak_count", "value": int(m.group(1)), "unit": "lesions"}),

        (r'(\d+)\s*(?:warts?|verruca)',
         lambda m: {"type": "wart_count", "value": int(m.group(1)), "unit": "lesions"}),

        (r'(\d+)\s*(?:lesions?|spots?|moles?|nevi)',
         lambda m: {"type": "lesion_count", "value": int(m.group(1)), "unit": "lesions"}),

        (r'(\d+)\s*(?:nails?)',
         lambda m: {"type": "nail_count", "value": int(m.group(1)), "unit": "nails"}),

        (r'(\d+)\s*(?:biops(?:y|ies))',
         lambda m: {"type": "biopsy_count", "value": int(m.group(1)), "unit": "biopsies"}),

        (r'(\d+)\s*(?:blocks?)',
         lambda m: {"type": "block_count", "value": int(m.group(1)), "unit": "blocks"}),

        (r'(\d+)\s*(?:stages?)',
         lambda m: {"type": "stage_count", "value": int(m.group(1)), "unit": "stages"}),
    ]

    for pattern, extractor in count_patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            try:
                measurement = extractor(match)
                measurement["context"] = text[max(0, match.start()-20):match.end()+20]
                measurements.append(measurement)
            except (ValueError, IndexError):
                continue

    return measurements


def parse_anatomic_sites_from_text(text: str) -> list[str]:
    """
    Extract anatomic sites from text using pattern matching.

    Args:
        text: Clinical note text

    Returns:
        List of anatomic sites found
    """
    sites = []

    # Common dermatology anatomic sites
    site_patterns = [
        # Head/Face
        r'\b(scalp|forehead|temple|face|cheek|chin|nose|nasal|ear|auricular|'
        r'periorbital|eyelid|lip|perioral|neck)\b',

        # Trunk
        r'\b(chest|back|trunk|abdomen|flank|shoulder|axilla|axillary|'
        r'breast|umbilical|gluteal|buttock)\b',

        # Extremities
        r'\b(arm|forearm|upper arm|elbow|wrist|hand|palm|finger|'
        r'leg|thigh|knee|shin|calf|ankle|foot|toe|heel|sole)\b',

        # Specific
        r'\b(dorsal hand|dorsal foot|plantar|palmar|interdigital|'
        r'nail|subungual|periungual)\b',

        # Directional
        r'\b(left|right|bilateral|anterior|posterior|medial|lateral)\s+'
        r'(scalp|forehead|temple|face|cheek|chin|nose|ear|neck|'
        r'chest|back|trunk|arm|forearm|hand|leg|thigh|foot)\b',
    ]

    for pattern in site_patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            site = match.group(0).strip().lower()
            if site not in sites:
                sites.append(site)

    return sites


def parse_procedures_from_text(text: str) -> list[str]:
    """
    Extract procedures from text using pattern matching.

    Args:
        text: Clinical note text

    Returns:
        List of procedures found
    """
    procedures = []

    procedure_patterns = [
        # Biopsies
        r'\b(shave biops[yies]+|punch biops[yies]+|incisional biops[yies]+|'
        r'excisional biops[yies]+|skin biops[yies]+)\b',

        # Destructions
        r'\b(cryotherapy|cryosurgery|liquid nitrogen|LN2|'
        r'electrodesiccation|curettage|ED&C|'
        r'destroyed?|destruction)\b',

        # Excisions
        r'\b(excision|excised|wide local excision|WLE|'
        r're-?excision|shave removal)\b',

        # Repairs
        r'\b(simple repair|intermediate repair|complex repair|'
        r'layered closure|primary closure|sutured?)\b',

        # Flaps/Grafts
        r'\b(advancement flap|rotation flap|transposition flap|'
        r'rhombic flap|bilobed flap|'
        r'FTSG|STSG|full thickness skin graft|split thickness skin graft)\b',

        # Mohs
        r'\b(Mohs|micrographic surgery)\b',

        # Injections
        r'\b(intralesional|IL injection|injected|triamcinolone|Kenalog|TAC)\b',

        # Other
        r'\b(debridement|I&D|incision and drainage|'
        r'chemical peel|phototherapy|UVB|PUVA|'
        r'patch test|nail (?:removal|avulsion|debridement))\b',
    ]

    for pattern in procedure_patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            proc = match.group(0).strip()
            if proc.lower() not in [p.lower() for p in procedures]:
                procedures.append(proc)

    return procedures


def parse_diagnoses_from_text(text: str) -> list[str]:
    """
    Extract diagnoses from text using pattern matching.

    Args:
        text: Clinical note text

    Returns:
        List of diagnoses found
    """
    diagnoses = []

    diagnosis_patterns = [
        # Inflammatory conditions
        r'\b(psoriasis|plaque psoriasis|guttate psoriasis|'
        r'eczema|atopic dermatitis|contact dermatitis|'
        r'seborrheic dermatitis|rosacea|acne|acne vulgaris)\b',

        # Infections
        r'\b(onychomycosis|tinea|cellulitis|folliculitis|'
        r'herpes|warts?|verruca|molluscum)\b',

        # Neoplasms
        r'\b(melanoma|BCC|basal cell carcinoma|SCC|squamous cell carcinoma|'
        r'actinic keratosis|AK|seborrheic keratosis|SK|'
        r'dysplastic nev[ius]+|atypical nev[ius]+|'
        r'lipoma|cyst|epidermal cyst|pilar cyst)\b',

        # Other
        r'\b(alopecia|vitiligo|hidradenitis|HS|pruritus|'
        r'urticaria|lichen planus|morphea)\b',
    ]

    for pattern in diagnosis_patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            dx = match.group(0).strip()
            if dx.lower() not in [d.lower() for d in diagnoses]:
                diagnoses.append(dx)

    return diagnoses


def parse_medications_from_text(text: str) -> list[str]:
    """
    Extract medications from text using pattern matching.

    Args:
        text: Clinical note text

    Returns:
        List of medications found
    """
    medications = []

    medication_patterns = [
        # Topical steroids
        r'\b(triamcinolone|clobetasol|betamethasone|hydrocortisone|'
        r'fluocinonide|mometasone|desonide)\b',

        # Topical non-steroids
        r'\b(tacrolimus|pimecrolimus|calcipotriene|'
        r'tretinoin|adapalene|benzoyl peroxide|'
        r'metronidazole|ivermectin|azelaic acid)\b',

        # Oral medications
        r'\b(doxycycline|minocycline|isotretinoin|accutane|'
        r'methotrexate|acitretin|prednisone|'
        r'hydroxychloroquine|mycophenolate)\b',

        # Biologics
        r'\b(Humira|adalimumab|Enbrel|etanercept|'
        r'Stelara|ustekinumab|Cosentyx|secukinumab|'
        r'Dupixent|dupilumab|Skyrizi|risankizumab)\b',

        # Injectables
        r'\b(Kenalog|triamcinolone acetonide|TAC|'
        r'5-FU|fluorouracil|bleomycin)\b',
    ]

    for pattern in medication_patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            med = match.group(0).strip()
            if med.lower() not in [m.lower() for m in medications]:
                medications.append(med)

    return medications


def extract_time_documentation(text: str) -> Optional[str]:
    """
    Extract time documentation from text.

    Args:
        text: Clinical note text

    Returns:
        Time documentation string or None
    """
    time_patterns = [
        r'total (?:visit |encounter )?time[:\s]+(\d+)\s*(?:minutes?|mins?)',
        r'(\d+)\s*(?:minutes?|mins?)\s*(?:spent|total)',
        r'time spent[:\s]+(\d+)\s*(?:minutes?|mins?)',
        r'counseling[:\s]+(\d+)\s*(?:minutes?|mins?)',
    ]

    for pattern in time_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(0)

    return None


def extract_entities_regex(note_text: str) -> ExtractedEntities:
    """
    Extract entities using regex patterns (fallback/supplement to LLM).

    Args:
        note_text: Clinical note text

    Returns:
        ExtractedEntities object
    """
    return ExtractedEntities(
        diagnoses=parse_diagnoses_from_text(note_text),
        procedures=parse_procedures_from_text(note_text),
        anatomic_sites=parse_anatomic_sites_from_text(note_text),
        measurements=parse_measurements_from_text(note_text),
        medications=parse_medications_from_text(note_text),
        time_documentation=extract_time_documentation(note_text),
        raw_entities=[],
    )


def merge_entities(llm_entities: ExtractedEntities, regex_entities: ExtractedEntities) -> ExtractedEntities:
    """
    Merge entities from LLM and regex extraction.

    Args:
        llm_entities: Entities extracted by LLM
        regex_entities: Entities extracted by regex

    Returns:
        Merged ExtractedEntities object
    """
    # Combine and deduplicate
    def unique_lower(items: list[str]) -> list[str]:
        seen = set()
        result = []
        for item in items:
            if item.lower() not in seen:
                seen.add(item.lower())
                result.append(item)
        return result

    return ExtractedEntities(
        diagnoses=unique_lower(llm_entities.diagnoses + regex_entities.diagnoses),
        procedures=unique_lower(llm_entities.procedures + regex_entities.procedures),
        anatomic_sites=unique_lower(llm_entities.anatomic_sites + regex_entities.anatomic_sites),
        measurements=llm_entities.measurements + regex_entities.measurements,  # Keep all measurements
        medications=unique_lower(llm_entities.medications + regex_entities.medications),
        time_documentation=llm_entities.time_documentation or regex_entities.time_documentation,
        raw_entities=llm_entities.raw_entities,
    )


def get_extraction_prompt(note_text: str) -> str:
    """
    Get the prompt for LLM entity extraction.

    Args:
        note_text: Clinical note text

    Returns:
        Formatted prompt string
    """
    return ENTITY_EXTRACTION_PROMPT.format(note_text=note_text)
