"""
LLM Integration Module

Handles all interactions with the Anthropic Claude API for:
- Entity extraction
- Billing analysis
- Documentation enhancement suggestions
- Future opportunity identification
"""

import os
import json
from typing import Optional
from pathlib import Path

from anthropic import Anthropic

from .models import (
    ExtractedEntities,
    CurrentBilling,
    BillingCode,
    DocumentationEnhancements,
    DocumentationEnhancement,
    FutureOpportunities,
    FutureOpportunity,
    PotentialCode,
)
from .entities import get_extraction_prompt, extract_entities_regex, merge_entities


class LLMClient:
    """Client for LLM-powered billing analysis."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ):
        """
        Initialize the LLM client.

        Args:
            api_key: Anthropic API key. If None, uses ANTHROPIC_API_KEY env var.
            model: Model to use. If None, uses ANTHROPIC_MODEL env var or default.
        """
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")

        self.model = model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
        self.client = Anthropic(api_key=self.api_key)

    def _call_llm(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> str:
        """
        Make a call to the LLM.

        Args:
            prompt: User prompt
            system: System prompt
            max_tokens: Maximum tokens in response
            temperature: Temperature for sampling

        Returns:
            LLM response text
        """
        messages = [{"role": "user", "content": prompt}]

        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages,
            "temperature": temperature,
        }

        if system:
            kwargs["system"] = system

        response = self.client.messages.create(**kwargs)
        return response.content[0].text

    def _parse_json_response(self, response: str) -> dict:
        """
        Parse JSON from LLM response, handling markdown code blocks.

        Args:
            response: LLM response text

        Returns:
            Parsed JSON dict
        """
        # Try to extract JSON from markdown code blocks
        if "```json" in response:
            start = response.index("```json") + 7
            end = response.index("```", start)
            response = response[start:end].strip()
        elif "```" in response:
            start = response.index("```") + 3
            end = response.index("```", start)
            response = response[start:end].strip()

        return json.loads(response)

    def extract_entities(self, note_text: str) -> ExtractedEntities:
        """
        Extract entities from a clinical note.

        Args:
            note_text: Clinical note text

        Returns:
            ExtractedEntities object
        """
        prompt = get_extraction_prompt(note_text)

        system = """You are a medical billing expert specializing in dermatology.
Your task is to extract all relevant billing entities from clinical notes.
Always respond with valid JSON only, no markdown formatting or explanation."""

        try:
            response = self._call_llm(prompt, system=system)
            data = self._parse_json_response(response)

            # Ensure measurements is a list of dicts
            measurements = data.get("measurements", [])
            if isinstance(measurements, dict):
                measurements = [measurements]
            elif not isinstance(measurements, list):
                measurements = []
            # Ensure each measurement is a dict
            measurements = [m if isinstance(m, dict) else {} for m in measurements]

            llm_entities = ExtractedEntities(
                diagnoses=data.get("diagnoses", []) or [],
                procedures=data.get("procedures", []) or [],
                anatomic_sites=data.get("anatomic_sites", []) or [],
                measurements=measurements,
                medications=data.get("medications", []) or [],
                time_documentation=data.get("time_documentation"),
                raw_entities=[],
            )
        except Exception:
            # Fallback to regex-only extraction
            llm_entities = ExtractedEntities(
                diagnoses=[],
                procedures=[],
                anatomic_sites=[],
                measurements=[],
                medications=[],
                time_documentation=None,
                raw_entities=[],
            )

        # Supplement with regex extraction
        regex_entities = extract_entities_regex(note_text)
        return merge_entities(llm_entities, regex_entities)

    def analyze_current_billing(
        self,
        note_text: str,
        entities: ExtractedEntities,
        corpus_context: str,
    ) -> CurrentBilling:
        """
        Analyze what can be billed from the note as written.

        Args:
            note_text: Original clinical note
            entities: Extracted entities
            corpus_context: Relevant corpus content (codes, rules)

        Returns:
            CurrentBilling object
        """
        prompt = f"""Analyze this dermatology clinical note and determine all billable codes.

CLINICAL NOTE:
{note_text}

EXTRACTED ENTITIES:
{json.dumps(entities.model_dump(), indent=2)}

REFERENCE INFORMATION:
{corpus_context}

For each billable service, provide:
1. CPT/HCPCS code
2. Modifier if needed (e.g., -25 for E/M with procedure)
3. Description
4. wRVU value
5. Status: "supported" if documentation is complete, "missing_documentation" if gaps exist

Also identify any documentation gaps that prevent billing.

Respond with JSON in this format:
{{
    "codes": [
        {{"code": "99214", "modifier": "-25", "description": "...", "wRVU": 1.92, "units": 1, "status": "supported", "documentation_note": null}},
        ...
    ],
    "total_wRVU": 3.45,
    "documentation_gaps": ["Gap 1", "Gap 2"]
}}"""

        system = """You are a dermatology medical billing expert.
Analyze clinical notes to identify all legitimately billable codes.
Be thorough but only include codes that are supportable by the documentation.
Apply proper modifier logic and NCCI edit rules.
Respond with valid JSON only."""

        try:
            response = self._call_llm(prompt, system=system)
            data = self._parse_json_response(response)

            codes = [
                BillingCode(
                    code=c["code"],
                    modifier=c.get("modifier"),
                    description=c.get("description", ""),
                    wRVU=float(c.get("wRVU", 0)),
                    units=int(c.get("units", 1)),
                    status=c.get("status", "supported"),
                    documentation_note=c.get("documentation_note"),
                )
                for c in data.get("codes", [])
            ]

            return CurrentBilling(
                codes=codes,
                total_wRVU=float(data.get("total_wRVU", sum(c.wRVU * c.units for c in codes))),
                documentation_gaps=data.get("documentation_gaps", []),
            )
        except Exception as e:
            return CurrentBilling(
                codes=[],
                total_wRVU=0.0,
                documentation_gaps=[f"Error analyzing billing: {str(e)}"],
            )

    def identify_enhancements(
        self,
        note_text: str,
        entities: ExtractedEntities,
        current_billing: CurrentBilling,
        corpus_context: str,
    ) -> DocumentationEnhancements:
        """
        Identify documentation enhancements to increase billing.

        Args:
            note_text: Original clinical note
            entities: Extracted entities
            current_billing: Current billing analysis
            corpus_context: Relevant corpus content

        Returns:
            DocumentationEnhancements object
        """
        prompt = f"""Analyze this clinical note and suggest documentation enhancements to capture additional legitimate revenue.

CLINICAL NOTE:
{note_text}

CURRENT BILLING:
{json.dumps(current_billing.model_dump(), indent=2)}

REFERENCE INFORMATION:
{corpus_context}

For each enhancement opportunity:
1. Identify the documentation gap
2. Provide specific language to add
3. Calculate the wRVU improvement

Also provide:
1. A complete suggested addendum
2. A fully optimized version of the note (copy-pasteable plain text)

Respond with JSON:
{{
    "enhancements": [
        {{
            "issue": "Closure type not documented",
            "current_code": "12001",
            "current_wRVU": 0.82,
            "suggested_addition": "Add: 'Wound edges undermined. Layered closure with deep dermal 4-0 Vicryl.'",
            "enhanced_code": "12031",
            "enhanced_wRVU": 1.95,
            "delta_wRVU": 1.13,
            "priority": "high"
        }}
    ],
    "suggested_addendum": "Addendum: ...",
    "optimized_note": "Complete optimized note text...",
    "enhanced_total_wRVU": 4.50,
    "improvement": 1.13
}}"""

        system = """You are a dermatology billing optimization expert.
Identify documentation improvements that would allow legitimate additional billing.
Focus on capturing work that was actually performed but not fully documented.
Never suggest adding documentation for work that wasn't done.
Provide specific, copy-pasteable language additions.
Respond with valid JSON only."""

        try:
            response = self._call_llm(prompt, system=system, max_tokens=8192)
            data = self._parse_json_response(response)

            enhancements = [
                DocumentationEnhancement(
                    issue=e["issue"],
                    current_code=e.get("current_code"),
                    current_wRVU=float(e.get("current_wRVU", 0)),
                    suggested_addition=e["suggested_addition"],
                    enhanced_code=e.get("enhanced_code"),
                    enhanced_wRVU=float(e.get("enhanced_wRVU", 0)),
                    delta_wRVU=float(e.get("delta_wRVU", 0)),
                    priority=e.get("priority", "medium"),
                )
                for e in data.get("enhancements", [])
            ]

            return DocumentationEnhancements(
                enhancements=enhancements,
                suggested_addendum=data.get("suggested_addendum"),
                optimized_note=data.get("optimized_note"),
                enhanced_total_wRVU=float(data.get("enhanced_total_wRVU", 0)),
                improvement=float(data.get("improvement", 0)),
            )
        except Exception as e:
            return DocumentationEnhancements(
                enhancements=[],
                suggested_addendum=None,
                optimized_note=None,
                enhanced_total_wRVU=current_billing.total_wRVU,
                improvement=0.0,
            )

    def identify_opportunities(
        self,
        note_text: str,
        entities: ExtractedEntities,
        scenario_content: str,
        corpus_context: str,
    ) -> FutureOpportunities:
        """
        Identify future opportunities ("next time" recommendations).

        Args:
            note_text: Original clinical note
            entities: Extracted entities
            scenario_content: Matched scenario file content
            corpus_context: Additional corpus context

        Returns:
            FutureOpportunities object
        """
        prompt = f"""Analyze this clinical note and identify opportunities for future visits.
These are things the provider could have done or looked for that would generate additional legitimate revenue.

CLINICAL NOTE:
{note_text}

EXTRACTED ENTITIES:
{json.dumps(entities.model_dump(), indent=2)}

RELEVANT SCENARIO GUIDANCE:
{scenario_content}

REFERENCE INFORMATION:
{corpus_context}

For each opportunity:
1. Category: comorbidity, procedure, visit_level, or documentation
2. What was found in the note
3. What opportunity was missed
4. What to do next time
5. Potential code and wRVU if action is taken
6. Teaching point explanation

Also provide:
1. An optimized version of the note as if these opportunities were captured (copy-pasteable plain text)

Respond with JSON:
{{
    "opportunities": [
        {{
            "category": "comorbidity",
            "finding": "Psoriasis documented",
            "opportunity": "Nail involvement not assessed",
            "action": "Examine nails for pitting, onycholysis",
            "potential_code": {{"code": "11721", "description": "Nail debridement 6+", "wRVU": 0.53}},
            "teaching_point": "50% of psoriasis patients have nail involvement."
        }}
    ],
    "optimized_note": "Complete optimized note with all opportunities captured...",
    "total_potential_additional_wRVU": 1.50
}}"""

        system = """You are a dermatology billing educator.
Identify missed opportunities that could generate legitimate additional revenue.
Focus on:
1. Comorbidities that should be screened for
2. Procedures that could have been performed
3. Visit level optimizations
4. Documentation improvements

These are TEACHING moments for the provider.
Be specific about what to look for and do next time.
Respond with valid JSON only."""

        try:
            response = self._call_llm(prompt, system=system, max_tokens=8192)
            data = self._parse_json_response(response)

            opportunities = []
            for o in data.get("opportunities", []):
                potential_code = None
                if o.get("potential_code"):
                    pc = o["potential_code"]
                    potential_code = PotentialCode(
                        code=pc["code"],
                        description=pc.get("description", ""),
                        wRVU=float(pc.get("wRVU", 0)),
                    )

                opportunities.append(FutureOpportunity(
                    category=o["category"],
                    finding=o["finding"],
                    opportunity=o["opportunity"],
                    action=o["action"],
                    potential_code=potential_code,
                    teaching_point=o["teaching_point"],
                ))

            return FutureOpportunities(
                opportunities=opportunities,
                optimized_note=data.get("optimized_note"),
                total_potential_additional_wRVU=float(data.get("total_potential_additional_wRVU", 0)),
            )
        except Exception as e:
            return FutureOpportunities(
                opportunities=[],
                optimized_note=None,
                total_potential_additional_wRVU=0.0,
            )


# Global instance
_llm_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    """Get the global LLM client instance."""
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client


def reset_llm_client() -> None:
    """Reset the global LLM client (useful for testing)."""
    global _llm_client
    _llm_client = None
