"""
Scenario Matcher Module

Matches clinical conditions found in notes to the appropriate scenario files
and returns relevant optimization opportunities.
"""

import os
import re
from pathlib import Path
from typing import Optional
from dataclasses import dataclass


@dataclass
class ScenarioMatch:
    """A matched clinical scenario."""
    name: str
    filename: str
    content: str
    match_score: float
    matched_terms: list[str]


class ScenarioMatcher:
    """Matches clinical notes to scenario playbooks."""

    # Mapping of condition keywords to scenario files
    CONDITION_MAPPINGS = {
        "Scenario_Acne": [
            "acne", "comedones", "comedonal", "cystic acne", "acne vulgaris",
            "papulopustular", "nodular acne", "isotretinoin", "accutane",
            "pimples", "breakouts", "acneiform"
        ],
        "Scenario_Psoriasis": [
            "psoriasis", "plaque psoriasis", "guttate", "pustular psoriasis",
            "erythrodermic psoriasis", "psoriatic", "psa", "psoriatic arthritis",
            "koebner", "auspitz"
        ],
        "Scenario_Eczema": [
            "eczema", "atopic dermatitis", "atopic", "ad ", "xerosis",
            "lichenification", "lichenified", "pruritic dermatitis",
            "dupixent", "dupilumab"
        ],
        "Scenario_Seborrheic_Dermatitis": [
            "seborrheic dermatitis", "seborrhea", "seb derm", "dandruff",
            "cradle cap", "sebopsoriasis"
        ],
        "Scenario_Rosacea": [
            "rosacea", "erythematotelangiectatic", "papulopustular rosacea",
            "rhinophyma", "phymatous", "ocular rosacea", "facial flushing",
            "telangiectasia"
        ],
        "Scenario_Contact_Dermatitis": [
            "contact dermatitis", "allergic contact", "acd", "irritant contact",
            "icd", "patch test", "nickel allergy", "occupational dermatitis"
        ],
        "Scenario_HS": [
            "hidradenitis", "hidradenitis suppurativa", "hs", "hurley",
            "sinus tract", "axillary abscess", "groin abscess", "apocrine"
        ],
        "Scenario_Alopecia": [
            "alopecia", "alopecia areata", "androgenetic", "aga", "telogen",
            "telogen effluvium", "hair loss", "thinning hair", "bald",
            "scarring alopecia", "lichen planopilaris"
        ],
        "Scenario_Vitiligo": [
            "vitiligo", "depigmentation", "leukoderma", "amelanotic",
            "repigmentation", "phototherapy for vitiligo"
        ],
        "Scenario_Skin_Check": [
            "skin check", "full body exam", "skin cancer screening",
            "total body exam", "mole check", "tbse", "atypical moles",
            "dysplastic nevi"
        ],
        "Scenario_Skin_Cancer_Surveillance": [
            "skin cancer surveillance", "melanoma follow", "bcc follow",
            "scc follow", "skin cancer history", "melanoma history",
            "post-mohs", "cancer surveillance"
        ],
        "Scenario_AK_Treatment": [
            "actinic keratosis", "ak", "aks", "actinic keratoses",
            "solar keratosis", "precancerous", "cryotherapy ak",
            "field treatment", "efudex", "fluorouracil"
        ],
        "Scenario_Wart_Treatment": [
            "wart", "warts", "verruca", "verrucae", "hpv", "plantar wart",
            "common wart", "flat wart", "condyloma", "molluscum"
        ],
        "Scenario_Nail_Disorder": [
            "nail", "onychomycosis", "fungal nail", "nail dystrophy",
            "onycholysis", "ingrown nail", "paronychia", "melanonychia",
            "nail pitting", "subungual"
        ],
        "Scenario_Wound_Care": [
            "wound", "ulcer", "leg ulcer", "venous ulcer", "diabetic wound",
            "pressure ulcer", "chronic wound", "wound care", "dehiscence",
            "debridement"
        ],
        "Scenario_Biopsy_Diagnostic": [
            "biopsy", "punch biopsy", "shave biopsy", "incisional biopsy",
            "diagnostic biopsy", "skin biopsy"
        ],
        "Scenario_Excision_Benign": [
            "benign excision", "cyst removal", "lipoma", "epidermal cyst",
            "pilar cyst", "benign lesion removal", "nevus excision",
            "sk removal", "seborrheic keratosis removal"
        ],
        "Scenario_Excision_Malignant": [
            "malignant excision", "melanoma excision", "bcc excision",
            "scc excision", "cancer excision", "wide local excision",
            "wle", "margin excision"
        ],
        "Scenario_Mohs_Surgery": [
            "mohs", "mohs surgery", "micrographic surgery",
            "mohs stage", "mohs block", "mohs defect", "mohs reconstruction"
        ],
        "Scenario_Patch_Testing": [
            "patch test", "patch testing", "allergen testing",
            "contact allergen", "t.r.u.e. test", "standard series"
        ],
        "Scenario_Phototherapy": [
            "phototherapy", "nbuvb", "nb-uvb", "puva", "light therapy",
            "uvb treatment", "phototherapy session"
        ],
        "Scenario_Pediatric": [
            "pediatric", "child", "infant", "baby", "toddler",
            "adolescent", "teenage", "diaper dermatitis", "cradle cap",
            "pediatric acne", "childhood eczema"
        ],
        "Scenario_Injection_Visit": [
            "injection", "il injection", "intralesional",
            "triamcinolone injection", "kenalog", "steroid injection",
            "keloid injection", "cyst injection"
        ],
        "Scenario_Pruritus": [
            "pruritus", "itching", "itchy", "itch", "prurigo",
            "generalized pruritus", "localized itch"
        ],
    }

    def __init__(self, scenarios_dir: Optional[str] = None):
        """
        Initialize the scenario matcher.

        Args:
            scenarios_dir: Path to scenarios directory. If None, uses default location.
        """
        if scenarios_dir is None:
            base_dir = Path(__file__).parent.parent
            scenarios_dir = base_dir / "scenarios"

        self.scenarios_dir = Path(scenarios_dir)
        self._scenario_cache: dict[str, str] = {}

    def list_scenarios(self) -> list[str]:
        """List all available scenario names."""
        scenarios = []
        if self.scenarios_dir.exists():
            for f in self.scenarios_dir.glob("Scenario_*.md"):
                name = f.stem.replace("Scenario_", "").replace("_", " ")
                scenarios.append(name)
        return sorted(scenarios)

    def get_scenario_content(self, scenario_name: str) -> Optional[str]:
        """
        Get the content of a scenario file.

        Args:
            scenario_name: Scenario name (e.g., "Psoriasis" or "Scenario_Psoriasis")

        Returns:
            Scenario markdown content or None if not found
        """
        # Normalize the name
        if not scenario_name.startswith("Scenario_"):
            scenario_name = f"Scenario_{scenario_name.replace(' ', '_')}"

        # Check cache first
        if scenario_name in self._scenario_cache:
            return self._scenario_cache[scenario_name]

        # Try to load the file
        filepath = self.scenarios_dir / f"{scenario_name}.md"
        if not filepath.exists():
            return None

        content = filepath.read_text(encoding="utf-8")
        self._scenario_cache[scenario_name] = content
        return content

    def match_scenarios(self, text: str, max_matches: int = 5) -> list[ScenarioMatch]:
        """
        Match clinical note text to relevant scenarios.

        Args:
            text: Clinical note text
            max_matches: Maximum number of scenarios to return

        Returns:
            List of ScenarioMatch objects, sorted by relevance
        """
        text_lower = text.lower()
        matches = []

        for scenario_name, keywords in self.CONDITION_MAPPINGS.items():
            matched_terms = []
            score = 0.0

            for keyword in keywords:
                # Use word boundary matching for more accurate results
                pattern = r'\b' + re.escape(keyword.lower()) + r'\b'
                if re.search(pattern, text_lower):
                    matched_terms.append(keyword)
                    # Longer keywords get higher scores
                    score += len(keyword.split())

            if matched_terms:
                content = self.get_scenario_content(scenario_name)
                if content:
                    matches.append(ScenarioMatch(
                        name=scenario_name.replace("Scenario_", "").replace("_", " "),
                        filename=f"{scenario_name}.md",
                        content=content,
                        match_score=score,
                        matched_terms=matched_terms,
                    ))

        # Sort by score descending
        matches.sort(key=lambda x: x.match_score, reverse=True)
        return matches[:max_matches]

    def get_relevant_scenarios_for_conditions(self, conditions: list[str]) -> list[ScenarioMatch]:
        """
        Get relevant scenarios for a list of conditions.

        Args:
            conditions: List of condition/diagnosis names

        Returns:
            List of matching scenarios
        """
        # Join conditions and match
        combined_text = " ".join(conditions)
        return self.match_scenarios(combined_text)

    def extract_opportunities_from_scenario(self, scenario_content: str) -> dict:
        """
        Extract structured opportunities from a scenario markdown file.

        Args:
            scenario_content: The scenario markdown content

        Returns:
            Dict with extracted opportunities
        """
        opportunities = {
            "procedure_opportunities": [],
            "comorbidities_to_check": [],
            "documentation_tips": [],
            "high_value_codes": [],
            "teaching_points": [],
        }

        # Extract sections using markdown headers
        sections = re.split(r'\n##\s+', scenario_content)

        for section in sections:
            section_lower = section.lower()

            # Extract procedure opportunities
            if "procedure" in section_lower or "opportunity" in section_lower:
                # Look for code patterns (5 digits or 5 chars like G2211)
                codes = re.findall(r'\b([0-9]{5}|[A-Z][0-9]{4})\b', section)
                opportunities["procedure_opportunities"].extend(codes)

            # Extract comorbidities
            if "comorbid" in section_lower or "look for" in section_lower:
                # Look for bulleted items
                items = re.findall(r'[-*]\s+(.+?)(?:\n|$)', section)
                opportunities["comorbidities_to_check"].extend(items)

            # Extract teaching points
            if "teaching" in section_lower or "next time" in section_lower:
                items = re.findall(r'[-*>]\s*"?(.+?)"?\s*(?:\n|$)', section)
                opportunities["teaching_points"].extend(items)

        # Remove duplicates while preserving order
        for key in opportunities:
            opportunities[key] = list(dict.fromkeys(opportunities[key]))

        return opportunities


# Global instance
_matcher_instance: Optional[ScenarioMatcher] = None


def get_scenario_matcher() -> ScenarioMatcher:
    """Get the global scenario matcher instance."""
    global _matcher_instance
    if _matcher_instance is None:
        _matcher_instance = ScenarioMatcher()
    return _matcher_instance


def match_note_to_scenarios(note_text: str) -> list[ScenarioMatch]:
    """Convenience function to match a note to scenarios."""
    return get_scenario_matcher().match_scenarios(note_text)
