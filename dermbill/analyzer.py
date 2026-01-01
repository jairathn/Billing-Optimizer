"""
Main Analysis Engine

Orchestrates the four-step billing optimization workflow:
1. Entity Extraction
2. Current Maximum Billing
3. Documentation Enhancement
4. Future Opportunities
"""

import os
from pathlib import Path
from typing import Optional

from .models import (
    AnalysisResult,
    ExtractedEntities,
    CurrentBilling,
    DocumentationEnhancements,
    FutureOpportunities,
)
from .codes import CPTCodeDatabase, get_code_database
from .scenarios import ScenarioMatcher, get_scenario_matcher
from .rules import is_g2211_eligible
from .llm import LLMClient, get_llm_client


class DermBillAnalyzer:
    """Main analyzer for dermatology billing optimization."""

    def __init__(
        self,
        corpus_dir: Optional[str] = None,
        llm_client: Optional[LLMClient] = None,
    ):
        """
        Initialize the analyzer.

        Args:
            corpus_dir: Path to corpus directory. If None, uses default.
            llm_client: LLM client instance. If None, creates one.
        """
        if corpus_dir is None:
            corpus_dir = Path(__file__).parent.parent

        self.corpus_dir = Path(corpus_dir)
        self.code_db = CPTCodeDatabase(self.corpus_dir / "CPT_Master_Reference.xlsx")
        self.scenario_matcher = ScenarioMatcher(self.corpus_dir / "scenarios")
        self.llm_client = llm_client

        # Load corpus files
        self._clinical_insights: Optional[str] = None
        self._rules_content: dict[str, str] = {}

    def _get_llm_client(self) -> LLMClient:
        """Get or create LLM client."""
        if self.llm_client is None:
            self.llm_client = get_llm_client()
        return self.llm_client

    def _load_clinical_insights(self) -> str:
        """Load the Clinical_Billing_Insights.md file."""
        if self._clinical_insights is None:
            filepath = self.corpus_dir / "Clinical_Billing_Insights.md"
            if filepath.exists():
                self._clinical_insights = filepath.read_text(encoding="utf-8")
            else:
                self._clinical_insights = ""
        return self._clinical_insights

    def _load_rules(self, rule_names: list[str]) -> str:
        """
        Load specified rule files.

        Args:
            rule_names: List of rule file names (without .md extension)

        Returns:
            Combined rule content
        """
        content_parts = []
        rules_dir = self.corpus_dir / "rules"

        for name in rule_names:
            if name not in self._rules_content:
                filepath = rules_dir / f"{name}.md"
                if filepath.exists():
                    self._rules_content[name] = filepath.read_text(encoding="utf-8")
                else:
                    self._rules_content[name] = ""

            if self._rules_content[name]:
                content_parts.append(f"## {name}\n{self._rules_content[name]}")

        return "\n\n".join(content_parts)

    def _build_corpus_context(
        self,
        entities: ExtractedEntities,
        include_rules: list[str],
    ) -> str:
        """
        Build context from corpus for LLM prompts.

        Args:
            entities: Extracted entities to match scenarios
            include_rules: Rule files to include

        Returns:
            Combined corpus context string
        """
        context_parts = []

        # Add relevant code information
        code_db = self.code_db
        code_db.load()

        # Get category info for relevant procedures
        categories_seen = set()
        for proc in entities.procedures:
            proc_lower = proc.lower()
            if "biopsy" in proc_lower:
                categories_seen.add("Biopsy")
            elif "excision" in proc_lower:
                categories_seen.add("Excision")
            elif "destruct" in proc_lower or "cryo" in proc_lower:
                categories_seen.add("Destruction")
            elif "repair" in proc_lower or "closure" in proc_lower:
                categories_seen.add("Repair")
            elif "flap" in proc_lower:
                categories_seen.add("Flap")
            elif "graft" in proc_lower:
                categories_seen.add("Graft")
            elif "mohs" in proc_lower:
                categories_seen.add("Mohs")

        for category in categories_seen:
            cat_info = code_db.get_category_info(category)
            if cat_info:
                context_parts.append(
                    f"### {cat_info['category']}\n"
                    f"Code Range: {cat_info['code_range']}\n"
                    f"Optimization: {cat_info['key_optimization_points']}"
                )

        # Add rules content
        rules_content = self._load_rules(include_rules)
        if rules_content:
            context_parts.append(f"## BILLING RULES\n{rules_content}")

        # Add clinical insights excerpt
        insights = self._load_clinical_insights()
        if insights:
            # Include relevant sections based on diagnoses
            context_parts.append("## CLINICAL BILLING INSIGHTS (Excerpt)")
            # For now, include a summary - could be more selective
            if len(insights) > 5000:
                context_parts.append(insights[:5000] + "...")
            else:
                context_parts.append(insights)

        return "\n\n".join(context_parts)

    def analyze(self, note_text: str) -> AnalysisResult:
        """
        Perform complete billing optimization analysis.

        Args:
            note_text: Clinical note text to analyze

        Returns:
            Complete AnalysisResult
        """
        llm = self._get_llm_client()

        # Step 1: Entity Extraction
        entities = llm.extract_entities(note_text)

        # Match scenarios based on entities
        scenario_matches = self.scenario_matcher.match_scenarios(note_text)
        scenario_content = ""
        if scenario_matches:
            scenario_content = scenario_matches[0].content
            # Add additional matches if relevant
            for match in scenario_matches[1:3]:
                scenario_content += f"\n\n---\n\n# Additional: {match.name}\n{match.content}"

        # Determine which rules to load based on procedures
        rules_to_load = ["Modifiers", "Medical_Necessity"]
        proc_text = " ".join(entities.procedures).lower()
        if any(x in proc_text for x in ["repair", "closure", "suture"]):
            rules_to_load.append("Repair_Aggregation")
        if any(x in proc_text for x in ["excision", "biopsy", "flap"]):
            rules_to_load.append("Measurement_Rules")
        if len(entities.procedures) > 1:
            rules_to_load.append("NCCI_Edits")

        # Build corpus context
        corpus_context = self._build_corpus_context(entities, rules_to_load)

        # Step 2: Current Maximum Billing
        current_billing = llm.analyze_current_billing(
            note_text,
            entities,
            corpus_context,
        )

        # Check G2211 eligibility
        if is_g2211_eligible(entities.diagnoses):
            # Check if G2211 is already in the billing
            has_g2211 = any(c.code == "G2211" for c in current_billing.codes)
            if not has_g2211:
                # Check if there's an E/M code to attach it to
                em_codes = ["99212", "99213", "99214", "99215"]
                has_em = any(c.code in em_codes for c in current_billing.codes)
                if has_em:
                    # Add G2211 as a documentation gap note
                    current_billing.documentation_gaps.append(
                        "G2211 (chronic condition add-on, +0.33 wRVU) may be applicable - ensure chronic condition is documented"
                    )

        # Step 3: Documentation Enhancement
        doc_enhancements = llm.identify_enhancements(
            note_text,
            entities,
            current_billing,
            corpus_context,
        )

        # Step 4: Future Opportunities
        future_opps = llm.identify_opportunities(
            note_text,
            entities,
            scenario_content,
            corpus_context,
        )

        return AnalysisResult(
            entities=entities,
            current_billing=current_billing,
            documentation_enhancements=doc_enhancements,
            future_opportunities=future_opps,
            original_note=note_text,
        )

    def lookup_code(self, code: str) -> Optional[dict]:
        """
        Look up a CPT/HCPCS code.

        Args:
            code: The code to look up

        Returns:
            Code information dict or None
        """
        result = self.code_db.get_code(code)
        return result.model_dump() if result else None

    def list_scenarios(self) -> list[str]:
        """List available clinical scenarios."""
        return self.scenario_matcher.list_scenarios()

    def get_scenario(self, name: str) -> Optional[str]:
        """Get scenario content by name."""
        return self.scenario_matcher.get_scenario_content(name)


# Convenience function for quick analysis
def analyze_note(note_text: str) -> AnalysisResult:
    """
    Analyze a clinical note for billing optimization.

    Args:
        note_text: Clinical note text

    Returns:
        Complete AnalysisResult
    """
    analyzer = DermBillAnalyzer()
    return analyzer.analyze(note_text)
