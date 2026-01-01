"""
Rules Engine Module

Implements billing rules including:
- Modifier logic
- Measurement rules for excisions, repairs, flaps
- NCCI edit checking
- Repair aggregation
- G2211 eligibility
"""

from typing import Optional
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class RepairComplexity(Enum):
    """Repair complexity levels."""
    SIMPLE = "simple"
    INTERMEDIATE = "intermediate"
    COMPLEX = "complex"


class AnatomicGroup(Enum):
    """Anatomic groups for repair aggregation."""
    GROUP_1 = "scalp_neck_trunk_extremities"  # Scalp, neck, axillae, trunk, extremities
    GROUP_2 = "face_ears_eyelids_nose_lips"   # Face, ears, eyelids, nose, lips, mucous membrane
    GROUP_3 = "hands_feet"                     # Hands, feet (for some code sets)


@dataclass
class RepairInfo:
    """Information about a repair."""
    length_cm: float
    complexity: RepairComplexity
    anatomic_site: str
    anatomic_group: AnatomicGroup


@dataclass
class AggregatedRepair:
    """Aggregated repair for billing."""
    complexity: RepairComplexity
    anatomic_group: AnatomicGroup
    total_length_cm: float
    code: str
    wRVU: float


# ============================================================================
# Chronic Conditions for G2211 Eligibility
# ============================================================================

CHRONIC_CONDITIONS = [
    "psoriasis", "eczema", "atopic dermatitis", "rosacea", "acne",
    "hidradenitis", "hidradenitis suppurativa", "alopecia", "vitiligo",
    "seborrheic dermatitis", "chronic urticaria", "lichen planus",
    "lichen sclerosus", "morphea", "scleroderma", "dermatomyositis",
    "lupus", "pemphigus", "pemphigoid", "epidermolysis bullosa",
    "ichthyosis", "xeroderma pigmentosum", "mycosis fungoides",
    "cutaneous lymphoma", "prurigo nodularis", "chronic pruritus",
]


def is_g2211_eligible(diagnoses: list[str]) -> bool:
    """
    Check if any diagnosis qualifies for G2211 add-on.

    Args:
        diagnoses: List of diagnosis/condition names

    Returns:
        True if G2211 is applicable
    """
    for diagnosis in diagnoses:
        diagnosis_lower = diagnosis.lower()
        for chronic in CHRONIC_CONDITIONS:
            if chronic in diagnosis_lower:
                return True
    return False


# ============================================================================
# Excision Measurement Rules
# ============================================================================

def calculate_excised_diameter(
    lesion_diameter_mm: float,
    margin_mm: float
) -> float:
    """
    Calculate excised specimen diameter.

    Formula: Excised diameter = Lesion diameter + (2 × narrowest margin)

    Args:
        lesion_diameter_mm: Lesion diameter in mm
        margin_mm: Narrowest margin in mm

    Returns:
        Excised diameter in cm
    """
    excised_mm = lesion_diameter_mm + (2 * margin_mm)
    return excised_mm / 10  # Convert to cm


def get_excision_code(
    excised_diameter_cm: float,
    anatomic_site: str,
    is_malignant: bool
) -> tuple[str, float]:
    """
    Get the appropriate excision code based on size and site.

    Args:
        excised_diameter_cm: Excised specimen diameter in cm
        anatomic_site: Body location
        is_malignant: True for malignant lesions

    Returns:
        Tuple of (CPT code, wRVU)
    """
    site_lower = anatomic_site.lower()

    # Determine site category
    is_face = any(s in site_lower for s in [
        "face", "ear", "eyelid", "nose", "lip", "cheek", "chin",
        "forehead", "temple", "periorbital"
    ])

    # Size thresholds (in cm)
    if is_malignant:
        if is_face:
            codes = [
                (0.5, "11640", 1.63),
                (1.0, "11641", 2.12),
                (2.0, "11642", 2.55),
                (3.0, "11643", 3.08),
                (4.0, "11644", 3.82),
                (float('inf'), "11646", 5.32),
            ]
        else:  # Trunk/extremities
            codes = [
                (0.5, "11600", 1.59),
                (1.0, "11601", 2.02),
                (2.0, "11602", 2.21),
                (3.0, "11603", 2.75),
                (4.0, "11604", 3.18),
                (float('inf'), "11606", 4.09),
            ]
    else:  # Benign
        if is_face:
            codes = [
                (0.5, "11440", 1.02),
                (1.0, "11441", 1.49),
                (2.0, "11442", 1.73),
                (3.0, "11443", 2.22),
                (4.0, "11444", 2.82),
                (float('inf'), "11446", 3.82),
            ]
        else:  # Trunk/extremities
            codes = [
                (0.5, "11400", 0.88),
                (1.0, "11401", 1.25),
                (2.0, "11402", 1.41),
                (3.0, "11403", 1.79),
                (4.0, "11404", 2.35),
                (float('inf'), "11406", 3.03),
            ]

    for threshold, code, wRVU in codes:
        if excised_diameter_cm <= threshold:
            return code, wRVU

    return codes[-1][1], codes[-1][2]


# ============================================================================
# Repair Aggregation Rules
# ============================================================================

def classify_anatomic_group(site: str) -> AnatomicGroup:
    """
    Classify an anatomic site into a repair aggregation group.

    Args:
        site: Body site description

    Returns:
        AnatomicGroup for repair aggregation
    """
    site_lower = site.lower()

    # Group 2: Face, ears, eyelids, nose, lips, mucous membrane
    group_2_terms = [
        "face", "ear", "eyelid", "nose", "lip", "cheek", "chin",
        "forehead", "temple", "periorbital", "perioral", "mucous",
        "vermilion", "nasal", "auricular"
    ]
    if any(term in site_lower for term in group_2_terms):
        return AnatomicGroup.GROUP_2

    # Group 1: Everything else (scalp, neck, trunk, extremities)
    return AnatomicGroup.GROUP_1


def get_repair_code(
    complexity: RepairComplexity,
    anatomic_group: AnatomicGroup,
    total_length_cm: float
) -> tuple[str, float]:
    """
    Get the appropriate repair code based on complexity, site, and length.

    Args:
        complexity: Repair complexity level
        anatomic_group: Anatomic grouping
        total_length_cm: Total aggregated repair length in cm

    Returns:
        Tuple of (CPT code, wRVU)
    """
    if complexity == RepairComplexity.SIMPLE:
        if anatomic_group == AnatomicGroup.GROUP_2:  # Face
            codes = [
                (2.5, "12011", 1.15),
                (5.0, "12013", 1.45),
                (7.5, "12014", 1.69),
                (12.5, "12015", 2.17),
                (20.0, "12016", 2.86),
                (30.0, "12017", 3.39),
                (float('inf'), "12018", 4.54),
            ]
        else:  # Trunk/extremities
            codes = [
                (2.5, "12001", 0.82),
                (7.5, "12002", 1.14),
                (12.5, "12004", 1.49),
                (20.0, "12005", 1.79),
                (30.0, "12006", 2.10),
                (float('inf'), "12007", 2.73),
            ]
    elif complexity == RepairComplexity.INTERMEDIATE:
        if anatomic_group == AnatomicGroup.GROUP_2:  # Face
            codes = [
                (2.5, "12051", 2.27),
                (5.0, "12052", 2.62),
                (7.5, "12053", 3.22),
                (12.5, "12054", 3.87),
                (20.0, "12055", 4.91),
                (30.0, "12056", 5.83),
                (float('inf'), "12057", 6.84),
            ]
        else:  # Trunk/extremities
            codes = [
                (2.5, "12031", 1.95),
                (5.0, "12032", 2.46),
                (7.5, "12034", 2.81),
                (12.5, "12035", 3.50),
                (20.0, "12036", 4.30),
                (30.0, "12037", 5.07),
                (float('inf'), "12038", 5.70),
            ]
    else:  # Complex
        if anatomic_group == AnatomicGroup.GROUP_2:  # Face
            codes = [
                (1.0, "13131", 3.64),
                (2.5, "13132", 4.52),
                (5.0, "13133", 5.79),
                (7.5, "13151", 4.23),  # Nose/ear specific
                (float('inf'), "13152", 5.61),
            ]
        else:  # Trunk/extremities
            codes = [
                (1.0, "13100", 2.60),
                (2.5, "13101", 3.25),
                (5.0, "13102", 4.25),
                (float('inf'), "13120", 3.35),  # Add-on each additional 5cm
            ]

    for threshold, code, wRVU in codes:
        if total_length_cm <= threshold:
            return code, wRVU

    return codes[-1][1], codes[-1][2]


def aggregate_repairs(repairs: list[RepairInfo]) -> list[AggregatedRepair]:
    """
    Aggregate repairs by complexity and anatomic group.

    Same complexity + same anatomic group = sum lengths and bill single code.

    Args:
        repairs: List of individual repairs

    Returns:
        List of aggregated repairs for billing
    """
    # Group by complexity + anatomic group
    groups: dict[tuple, list[RepairInfo]] = {}
    for repair in repairs:
        key = (repair.complexity, repair.anatomic_group)
        if key not in groups:
            groups[key] = []
        groups[key].append(repair)

    # Aggregate and get codes
    results = []
    for (complexity, anatomic_group), group_repairs in groups.items():
        total_length = sum(r.length_cm for r in group_repairs)
        code, wRVU = get_repair_code(complexity, anatomic_group, total_length)
        results.append(AggregatedRepair(
            complexity=complexity,
            anatomic_group=anatomic_group,
            total_length_cm=total_length,
            code=code,
            wRVU=wRVU,
        ))

    return results


# ============================================================================
# AK Destruction Coding
# ============================================================================

def get_ak_destruction_codes(count: int) -> list[tuple[str, int, float]]:
    """
    Get optimal AK destruction codes based on lesion count.

    Args:
        count: Number of AK lesions destroyed

    Returns:
        List of (code, units, wRVU) tuples
    """
    if count <= 0:
        return []

    if count >= 15:
        # Use flat-rate code for 15+
        return [("17004", 1, 2.59)]
    else:
        # First lesion + add-ons
        codes = [("17000", 1, 0.61)]
        if count > 1:
            # Add-on for lesions 2-14
            addon_count = count - 1
            codes.append(("17003", addon_count, 0.09 * addon_count))
        return codes


# ============================================================================
# Benign Lesion Destruction
# ============================================================================

def get_benign_destruction_codes(count: int, is_wart_or_molluscum: bool = True) -> list[tuple[str, int, float]]:
    """
    Get destruction codes for benign lesions (warts, molluscum, etc).

    Args:
        count: Number of lesions destroyed
        is_wart_or_molluscum: True for warts/molluscum, False for skin tags

    Returns:
        List of (code, units, wRVU) tuples
    """
    if count <= 0:
        return []

    if is_wart_or_molluscum:
        if count >= 15:
            return [("17111", 1, 0.79)]
        else:
            return [("17110", 1, 0.52)]
    else:
        # Skin tags
        codes = [("11200", 1, 0.80)]  # Up to 15 tags
        if count > 15:
            # Each additional 10 tags
            additional_groups = (count - 15 + 9) // 10
            codes.append(("11201", additional_groups, 0.28 * additional_groups))
        return codes


# ============================================================================
# Flap/Graft Measurement
# ============================================================================

def calculate_flap_size(
    primary_defect_sq_cm: float,
    secondary_defect_sq_cm: float
) -> float:
    """
    Calculate total flap size for billing.

    Formula: Total = Primary defect + Secondary defect

    Args:
        primary_defect_sq_cm: Primary defect size in sq cm
        secondary_defect_sq_cm: Secondary defect size in sq cm

    Returns:
        Total flap size in sq cm
    """
    return primary_defect_sq_cm + secondary_defect_sq_cm


def get_flap_code(total_sq_cm: float, site: str) -> tuple[str, float]:
    """
    Get flap code based on size and site.

    Args:
        total_sq_cm: Total flap size in sq cm
        site: Anatomic site

    Returns:
        Tuple of (CPT code, wRVU)
    """
    site_lower = site.lower()

    # Nose, ears, eyelids, lips
    if any(s in site_lower for s in ["nose", "ear", "eyelid", "lip"]):
        if total_sq_cm <= 10:
            return ("14060", 9.00)
        else:
            return ("14061", 11.19)

    # Forehead, cheeks, chin, mouth, neck
    elif any(s in site_lower for s in ["forehead", "cheek", "chin", "neck", "face"]):
        if total_sq_cm <= 10:
            return ("14040", 7.52)
        else:
            return ("14041", 9.50)

    # Scalp, trunk, extremities
    else:
        if total_sq_cm <= 10:
            return ("14000", 6.11)
        else:
            return ("14001", 7.50)


# ============================================================================
# Biopsy Coding
# ============================================================================

def get_biopsy_codes(
    shave_count: int = 0,
    punch_count: int = 0,
    incisional_count: int = 0
) -> list[tuple[str, int, float]]:
    """
    Get optimal biopsy codes.

    Args:
        shave_count: Number of shave biopsies
        punch_count: Number of punch biopsies
        incisional_count: Number of incisional biopsies

    Returns:
        List of (code, units, wRVU) tuples
    """
    codes = []

    # Shave biopsies: 11102 (first), 11103 (each additional)
    if shave_count > 0:
        codes.append(("11102", 1, 0.64))
        if shave_count > 1:
            codes.append(("11103", shave_count - 1, 0.37 * (shave_count - 1)))

    # Punch biopsies: 11104 (first), 11105 (each additional)
    if punch_count > 0:
        codes.append(("11104", 1, 0.81))
        if punch_count > 1:
            codes.append(("11105", punch_count - 1, 0.44 * (punch_count - 1)))

    # Incisional biopsies: 11106 (first), 11107 (each additional)
    if incisional_count > 0:
        codes.append(("11106", 1, 0.98))
        if incisional_count > 1:
            codes.append(("11107", incisional_count - 1, 0.54 * (incisional_count - 1)))

    return codes


# ============================================================================
# Intralesional Injection Coding
# ============================================================================

def get_il_injection_code(lesion_count: int) -> tuple[str, float]:
    """
    Get IL injection code based on lesion count.

    Args:
        lesion_count: Number of lesions injected

    Returns:
        Tuple of (CPT code, wRVU)
    """
    if lesion_count <= 7:
        return ("11900", 0.51)
    else:
        return ("11901", 0.78)


# ============================================================================
# NCCI Edit Checking
# ============================================================================

# Common bundled pairs (code1 bundles into code2)
NCCI_BUNDLES = {
    # E/M bundles - need -25 to unbundle
    ("99213", "17000"): "25",  # E/M with AK destruction
    ("99214", "17000"): "25",
    ("99215", "17000"): "25",
    ("99213", "11102"): "25",  # E/M with biopsy
    ("99214", "11102"): "25",
    ("99215", "11102"): "25",
    ("99213", "11104"): "25",
    ("99214", "11104"): "25",
    ("99215", "11104"): "25",

    # Repair bundles into excision - typically cannot unbundle
    ("12001", "11400"): None,
    ("12001", "11401"): None,

    # Multiple destruction - use appropriate add-on
    ("17000", "17003"): "addon",  # 17003 is add-on to 17000
}


def check_ncci_edit(code1: str, code2: str) -> Optional[str]:
    """
    Check if two codes have an NCCI edit.

    Args:
        code1: First CPT code
        code2: Second CPT code

    Returns:
        Modifier to use for unbundling, "addon" if add-on relationship,
        None if cannot unbundle, or "ok" if no edit exists
    """
    pair = (code1, code2)
    reverse_pair = (code2, code1)

    if pair in NCCI_BUNDLES:
        return NCCI_BUNDLES[pair]
    elif reverse_pair in NCCI_BUNDLES:
        return NCCI_BUNDLES[reverse_pair]
    else:
        return "ok"  # No known edit


# ============================================================================
# Modifier Logic
# ============================================================================

def needs_modifier_25(em_code: str, procedure_codes: list[str]) -> bool:
    """
    Check if -25 modifier is needed on E/M code.

    Args:
        em_code: The E/M code
        procedure_codes: List of procedure codes being billed

    Returns:
        True if -25 is needed
    """
    # -25 needed when billing E/M with any significant procedure
    em_codes = ["99202", "99203", "99204", "99205",
                "99211", "99212", "99213", "99214", "99215"]

    if em_code not in em_codes:
        return False

    # Check if any procedure would require unbundling
    for proc_code in procedure_codes:
        edit_result = check_ncci_edit(em_code, proc_code)
        if edit_result == "25":
            return True

    return len(procedure_codes) > 0


def get_modifier_guidance(modifier: str) -> dict:
    """
    Get guidance for using a modifier.

    Args:
        modifier: The modifier code (e.g., "25", "59")

    Returns:
        Dict with usage guidance
    """
    guidance = {
        "25": {
            "name": "Significant, Separately Identifiable E/M",
            "use_when": "E/M represents substantial, separate work beyond procedure decision",
            "document": "Clearly document E/M work separate from procedure",
            "audit_risk": "HIGH - Most audited modifier",
        },
        "59": {
            "name": "Distinct Procedural Service",
            "use_when": "Different site, organ system, incision, or encounter",
            "document": "Document distinct nature of each procedure",
            "audit_risk": "MEDIUM-HIGH - CMS prefers X modifiers",
        },
        "XE": {
            "name": "Separate Encounter",
            "use_when": "Service distinct because it occurred during separate encounter same day",
            "document": "Document separate encounter times",
            "audit_risk": "LOW - More specific than -59",
        },
        "XS": {
            "name": "Separate Structure",
            "use_when": "Different anatomic structure",
            "document": "Document specific anatomic structures",
            "audit_risk": "LOW",
        },
        "50": {
            "name": "Bilateral Procedure",
            "use_when": "Same procedure performed on both sides",
            "document": "Document bilateral nature",
            "audit_risk": "LOW",
        },
    }
    return guidance.get(modifier.lstrip("-"), {"name": "Unknown", "audit_risk": "Unknown"})
