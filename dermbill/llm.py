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
import asyncio
from typing import Optional
from pathlib import Path

from anthropic import Anthropic, AsyncAnthropic

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
        self.client = Anthropic(api_key=self.api_key, timeout=120.0)
        self.async_client = AsyncAnthropic(api_key=self.api_key, timeout=120.0)

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

    async def _call_llm_async(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> str:
        """Async version of _call_llm."""
        messages = [{"role": "user", "content": prompt}]

        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages,
            "temperature": temperature,
        }

        if system:
            kwargs["system"] = system

        response = await self.async_client.messages.create(**kwargs)
        return response.content[0].text

    def _parse_json_response(self, response: str) -> dict:
        """
        Parse JSON from LLM response, handling markdown code blocks.

        Args:
            response: LLM response text

        Returns:
            Parsed JSON dict
        """
        original_response = response

        # Try to extract JSON from markdown code blocks
        try:
            if "```json" in response:
                start = response.index("```json") + 7
                end = response.index("```", start)
                response = response[start:end].strip()
            elif "```" in response:
                start = response.index("```") + 3
                end = response.index("```", start)
                response = response[start:end].strip()
        except ValueError:
            # No closing ``` found, try to parse the whole response
            pass

        try:
            return json.loads(response)
        except json.JSONDecodeError as e:
            # Try to find any JSON object in the response
            import re
            json_match = re.search(r'\{[\s\S]*\}', original_response)
            if json_match:
                try:
                    return json.loads(json_match.group())
                except json.JSONDecodeError:
                    pass
            raise ValueError(f"Failed to parse JSON from LLM response: {e}. Response: {original_response[:500]}")

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
        corpus_context: str,
    ) -> tuple[CurrentBilling, DocumentationEnhancements]:
        """
        Analyze current billing AND identify documentation enhancements.

        Args:
            note_text: Original clinical note
            entities: Extracted entities
            corpus_context: Relevant corpus content

        Returns:
            Tuple of (CurrentBilling, DocumentationEnhancements)
        """
        prompt = f"""Analyze this dermatology note for billing optimization.

NOTE:
{note_text}

ENTITIES:
{json.dumps(entities.model_dump(), indent=2)}

REFERENCE:
{corpus_context}

TASK:
1. Identify ALL billable codes from note AS WRITTEN
2. Suggest DOCUMENTATION enhancements ONLY for work that WAS ACTUALLY PERFORMED

CRITICAL: If a procedure/exam/service WAS NOT DONE, it belongs in Step 4 (Opportunities), NOT here.

VALID Step 3 Enhancements (things that WERE done):
- G2211 add-on: Chronic condition relationship EXISTS → document it
- E/M upgrade: MDM/counseling DID happen → document complexity to support higher level
- Code upgrades: Repair WAS done → document technique for intermediate vs simple
- Unbundling: Multiple procedures WERE done → separate under different diagnoses

INVALID for Step 3 (move to Step 4):
- "Injection not documented" when NO injection was given
- "Exam not performed" → that's a missed opportunity, not an enhancement
- Any procedure that COULD have been done but WASN'T

JSON format:
{{"current_billing": {{"codes": [{{"code": "X", "modifier": "X", "description": "X", "wRVU": 0, "units": 1, "status": "supported"}}], "total_wRVU": 0, "documentation_gaps": []}},
"enhancements": [{{"issue": "X", "current_code": "X", "current_wRVU": 0, "suggested_addition": "X", "enhanced_code": "X", "enhanced_wRVU": 0, "delta_wRVU": 0, "priority": "high"}}],
"suggested_addendum": "X", "optimized_note": "X", "enhanced_total_wRVU": 0, "improvement": 0}}"""

        system = """Dermatology billing expert. Maximize billing through DOCUMENTATION of work ALREADY DONE.

CRITICAL: Only include enhancements for services that WERE PERFORMED.
- If procedure wasn't done → Step 4, not here
- If exam wasn't performed → Step 4, not here
- G2211, E/M upgrades, unbundling for work done → YES
Respond with valid JSON only."""

        try:
            response = self._call_llm(prompt, system=system, max_tokens=8192)
            data = self._parse_json_response(response)

            # Parse current billing
            cb_data = data.get("current_billing", {})
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
                for c in cb_data.get("codes", [])
            ]
            current_billing = CurrentBilling(
                codes=codes,
                total_wRVU=float(cb_data.get("total_wRVU", sum(c.wRVU * c.units for c in codes))),
                documentation_gaps=cb_data.get("documentation_gaps", []),
            )

            # Parse enhancements
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

            doc_enhancements = DocumentationEnhancements(
                enhancements=enhancements,
                suggested_addendum=data.get("suggested_addendum"),
                optimized_note=data.get("optimized_note"),
                enhanced_total_wRVU=float(data.get("enhanced_total_wRVU", 0)),
                improvement=float(data.get("improvement", 0)),
            )

            return current_billing, doc_enhancements
        except Exception as e:
            return (
                CurrentBilling(codes=[], total_wRVU=0.0, documentation_gaps=[f"Error: {str(e)}"]),
                DocumentationEnhancements(enhancements=[], enhanced_total_wRVU=0.0, improvement=0.0),
            )

    async def identify_enhancements_async(
        self,
        note_text: str,
        entities: ExtractedEntities,
        corpus_context: str,
    ) -> tuple[CurrentBilling, DocumentationEnhancements]:
        """Async version of identify_enhancements."""
        prompt = f"""Analyze this dermatology note for billing optimization.

NOTE:
{note_text}

ENTITIES:
{json.dumps(entities.model_dump(), indent=2)}

REFERENCE:
{corpus_context}

TASK:
1. Identify ALL billable codes from note AS WRITTEN
2. Suggest DOCUMENTATION enhancements ONLY for work that WAS ACTUALLY PERFORMED

CRITICAL: If a procedure/exam/service WAS NOT DONE, it belongs in Step 4 (Opportunities), NOT here.

VALID Step 3 Enhancements (things that WERE done):
- G2211 add-on: Chronic condition relationship EXISTS → document it
- E/M upgrade: MDM/counseling DID happen → document complexity to support higher level
- Code upgrades: Repair WAS done → document technique for intermediate vs simple
- Unbundling: Multiple procedures WERE done → separate under different diagnoses

INVALID for Step 3 (move to Step 4):
- "Injection not documented" when NO injection was given
- "Exam not performed" → that's a missed opportunity, not an enhancement
- Any procedure that COULD have been done but WASN'T

JSON format:
{{"current_billing": {{"codes": [{{"code": "X", "modifier": "X", "description": "X", "wRVU": 0, "units": 1, "status": "supported"}}], "total_wRVU": 0, "documentation_gaps": []}},
"enhancements": [{{"issue": "X", "current_code": "X", "current_wRVU": 0, "suggested_addition": "X", "enhanced_code": "X", "enhanced_wRVU": 0, "delta_wRVU": 0, "priority": "high"}}],
"suggested_addendum": "X", "optimized_note": "X", "enhanced_total_wRVU": 0, "improvement": 0}}"""

        system = """Dermatology billing expert. Maximize billing through DOCUMENTATION of work ALREADY DONE.

CRITICAL: Only include enhancements for services that WERE PERFORMED.
- If procedure wasn't done → Step 4, not here
- If exam wasn't performed → Step 4, not here
- G2211, E/M upgrades, unbundling for work done → YES
Respond with valid JSON only."""

        try:
            response = await self._call_llm_async(prompt, system=system, max_tokens=8192)
            data = self._parse_json_response(response)

            # Parse current billing
            cb_data = data.get("current_billing", {})
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
                for c in cb_data.get("codes", [])
            ]
            current_billing = CurrentBilling(
                codes=codes,
                total_wRVU=float(cb_data.get("total_wRVU", sum(c.wRVU * c.units for c in codes))),
                documentation_gaps=cb_data.get("documentation_gaps", []),
            )

            # Parse enhancements
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

            doc_enhancements = DocumentationEnhancements(
                enhancements=enhancements,
                suggested_addendum=data.get("suggested_addendum"),
                optimized_note=data.get("optimized_note"),
                enhanced_total_wRVU=float(data.get("enhanced_total_wRVU", 0)),
                improvement=float(data.get("improvement", 0)),
            )

            return current_billing, doc_enhancements
        except Exception as e:
            return (
                CurrentBilling(codes=[], total_wRVU=0.0, documentation_gaps=[f"Error: {str(e)}"]),
                DocumentationEnhancements(enhancements=[], enhanced_total_wRVU=0.0, improvement=0.0),
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
        prompt = f"""Identify INTRA-ENCOUNTER opportunities that could have increased billing.

NOTE:
{note_text}

ENTITIES:
{json.dumps(entities.model_dump(), indent=2)}

SCENARIO:
{scenario_content}

REFERENCE:
{corpus_context}

OPPORTUNITY TYPES (things that COULD have been done this visit):
1. E/M LEVEL UPGRADES: Discussions/counseling that upgrade 99213→99214→99215, or 99203→99205
   - Medication management discussions, treatment options counseling, risk/benefit discussions
2. ADDITIONAL CODES: In Mohs, add 99203 for complex closure discussion. Add G2211 for chronic conditions.
3. MISSED PROCEDURES: Nail debridement (6+), IL injections (8+), AK treatment (15+)
4. COMORBIDITY CAPTURE: Related conditions warranting separate billing

Every opportunity MUST have a specific CPT code and wRVU. Thresholds: nails 6+, IL 8+, AK/warts 15+

JSON format:
{{"opportunities": [{{"category": "visit_level|procedure|comorbidity", "finding": "X", "opportunity": "X", "action": "X", "potential_code": {{"code": "X", "description": "X", "wRVU": 0}}, "teaching_point": "X"}}],
"optimized_note": "X", "total_potential_additional_wRVU": 0}}"""

        system = """Dermatology billing educator. Identify what COULD have been done intra-encounter to boost billing.

Focus on:
- E/M level upgrades through discussions (medication management, counseling → 99215/99205)
- Additional billable codes (Mohs + 99203 for complex closure, G2211 for chronic)
- Missed procedures meeting thresholds
Every opportunity needs specific CPT code and wRVU. Respond with valid JSON only."""

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

    async def identify_opportunities_async(
        self,
        note_text: str,
        entities: ExtractedEntities,
        scenario_content: str,
        corpus_context: str,
    ) -> FutureOpportunities:
        """Async version of identify_opportunities."""
        prompt = f"""Identify INTRA-ENCOUNTER opportunities that could have increased billing.

NOTE:
{note_text}

ENTITIES:
{json.dumps(entities.model_dump(), indent=2)}

SCENARIO:
{scenario_content}

REFERENCE:
{corpus_context}

OPPORTUNITY TYPES (things that COULD have been done this visit):
1. E/M LEVEL UPGRADES: Discussions/counseling that upgrade 99213→99214→99215, or 99203→99205
   - Medication management discussions, treatment options counseling, risk/benefit discussions
2. ADDITIONAL CODES: In Mohs, add 99203 for complex closure discussion. Add G2211 for chronic conditions.
3. MISSED PROCEDURES: Nail debridement (6+), IL injections (8+), AK treatment (15+)
4. COMORBIDITY CAPTURE: Related conditions warranting separate billing

Every opportunity MUST have a specific CPT code and wRVU. Thresholds: nails 6+, IL 8+, AK/warts 15+

JSON format:
{{"opportunities": [{{"category": "visit_level|procedure|comorbidity", "finding": "X", "opportunity": "X", "action": "X", "potential_code": {{"code": "X", "description": "X", "wRVU": 0}}, "teaching_point": "X"}}],
"optimized_note": "X", "total_potential_additional_wRVU": 0}}"""

        system = """Dermatology billing educator. Identify what COULD have been done intra-encounter to boost billing.

Focus on:
- E/M level upgrades through discussions (medication management, counseling → 99215/99205)
- Additional billable codes (Mohs + 99203 for complex closure, G2211 for chronic)
- Missed procedures meeting thresholds
Every opportunity needs specific CPT code and wRVU. Respond with valid JSON only."""

        try:
            response = await self._call_llm_async(prompt, system=system, max_tokens=8192)
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

    async def regenerate_note_async(
        self,
        original_note: str,
        selected_enhancements: list[dict],
        selected_opportunities: list[dict],
        current_billing_codes: list[dict] = None,
    ) -> dict:
        """
        Regenerate an optimized note based on selected recommendations.

        Args:
            original_note: Original clinical note
            selected_enhancements: List of selected enhancement dicts
            selected_opportunities: List of selected opportunity dicts
            current_billing_codes: List of current billing codes from Step 2

        Returns:
            Dict with optimized_note, billing_codes, and total_wRVU
        """
        # Build the list of changes to apply
        changes_to_apply = []
        billing_codes = []

        # Start with current billing codes if provided
        if current_billing_codes:
            for c in current_billing_codes:
                billing_codes.append({
                    "code": c.get("code", ""),
                    "modifier": c.get("modifier"),
                    "description": c.get("description", ""),
                    "wRVU": float(c.get("wRVU", 0)),
                })

        # Process selected enhancements - update or add billing codes
        for e in selected_enhancements:
            changes_to_apply.append(f"ENHANCEMENT: {e.get('issue', '')} - {e.get('suggested_addition', '')}")
            # If enhancement has an enhanced_code, update billing
            enhanced_code = e.get("enhanced_code", "")
            if enhanced_code and enhanced_code != e.get("current_code", ""):
                # Add the enhanced code
                billing_codes.append({
                    "code": enhanced_code.split()[0] if " " in enhanced_code else enhanced_code,
                    "modifier": None,
                    "description": e.get("issue", ""),
                    "wRVU": float(e.get("enhanced_wRVU", 0)),
                })

        # Process selected opportunities - add their billing codes
        for o in selected_opportunities:
            changes_to_apply.append(f"OPPORTUNITY: {o.get('opportunity', '')} - {o.get('action', '')}")
            potential_code = o.get("potential_code", {})
            if potential_code and potential_code.get("code"):
                billing_codes.append({
                    "code": potential_code.get("code", ""),
                    "modifier": None,
                    "description": potential_code.get("description", ""),
                    "wRVU": float(potential_code.get("wRVU", 0)),
                })

        if not changes_to_apply:
            total_wRVU = sum(c.get("wRVU", 0) for c in billing_codes)
            return {
                "optimized_note": original_note,
                "billing_codes": billing_codes,
                "total_wRVU": total_wRVU,
            }

        prompt = f"""Rewrite this clinical note AS IF all selected recommendations were actually performed and documented.

ORIGINAL NOTE:
{original_note}

SELECTED ITEMS TO DOCUMENT (write as if these were all done):
{chr(10).join(changes_to_apply)}

INSTRUCTIONS:
1. Write the note AS IF all selected procedures/services were actually performed
2. For opportunities (things that weren't done): document them as if they WERE done
3. For enhancements: add the documentation details that support higher billing
4. The final note should fully support billing all selected codes
5. Keep the note professional and clinically appropriate
6. Output ONLY the complete rewritten note - no explanations

This is a TEMPLATE showing what the provider should document to bill these codes.

OUTPUT THE COMPLETE OPTIMIZED NOTE:"""

        system = """Medical documentation expert. Create notes that support maximum billing.

Write the note AS IF all selected items were actually performed during the visit.
- If an injection opportunity is selected, document that the injection WAS done
- If an E/M upgrade is selected, document the MDM complexity that supports it
- The note should be copy-paste ready to support billing all selected codes
Output only the complete note text, no commentary."""

        try:
            response = await self._call_llm_async(prompt, system=system, max_tokens=4096)
            total_wRVU = sum(c.get("wRVU", 0) for c in billing_codes)
            return {
                "optimized_note": response.strip(),
                "billing_codes": billing_codes,
                "total_wRVU": total_wRVU,
            }
        except Exception as e:
            return {
                "optimized_note": f"Error regenerating note: {str(e)}",
                "billing_codes": billing_codes,
                "total_wRVU": 0.0,
            }


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
