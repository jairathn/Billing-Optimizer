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
        prompt = f"""You are a dermatology billing optimization expert. Analyze this clinical note to identify MISSED billing opportunities - procedures/services that COULD have been performed but WERE NOT.

CLINICAL NOTE:
{note_text}

EXTRACTED ENTITIES:
{json.dumps(entities.model_dump(), indent=2)}

CLINICAL SCENARIO GUIDANCE:
{scenario_content}

BILLING REFERENCE:
{corpus_context}

YOUR TASK: Identify what the provider COULD HAVE DONE during this encounter to increase billing. These are NOT documentation fixes - these are actual clinical interventions that were missed.

═══════════════════════════════════════════════════════════════════════════════
CATEGORY 1: THERAPEUTIC INJECTIONS
═══════════════════════════════════════════════════════════════════════════════
CLINICAL TRIGGERS:
• Thick psoriasis plaques (especially if topicals failing)
• Recalcitrant eczema patches
• Keloids or hypertrophic scars
• Alopecia areata patches
• Lichen simplex chronicus
• Granuloma annulare
• Cystic acne

BILLING CODES (use potential_code with count):
• 11900: IL injection 1-7 lesions (0.52 wRVU)
• 11901: IL injection 8+ lesions (0.82 wRVU)

CRITICAL: Multiple injection sites for DIFFERENT conditions (e.g., psoriasis plaques + keloid)
are SEPARATE opportunities. Each becomes a count input that aggregates in the UI.

Example finding: "Thick psoriasis plaques on elbows not responding to topicals"
Example action: "IL triamcinolone 10mg/mL injection to thick plaques"

═══════════════════════════════════════════════════════════════════════════════
CATEGORY 2: DESTRUCTION - PREMALIGNANT (AKs)
═══════════════════════════════════════════════════════════════════════════════
CLINICAL TRIGGERS:
• Sun-damaged skin in sun-exposed areas
• History of skin cancer
• Actinic keratoses noted on exam
• Rough/scaly patches on face, scalp, arms, hands

BILLING CODES (use potential_code - count-based):
• 17000: First AK destruction (0.61 wRVU)
• 17003: Each additional AK 2-14 (+0.09 wRVU each)
• 17004: 15+ AKs flat rate (2.19 wRVU)

Example: 10 AKs = 17000 + 17003x9 = 0.61 + 0.81 = 1.42 wRVU

═══════════════════════════════════════════════════════════════════════════════
CATEGORY 3: DESTRUCTION - BENIGN LESIONS
═══════════════════════════════════════════════════════════════════════════════
CLINICAL TRIGGERS:
• Seborrheic keratoses (symptomatic - itching, irritation, catching on clothes)
• Skin tags in friction areas
• Verrucae (warts)
• Molluscum contagiosum
• Cherry angiomas (if symptomatic)

BILLING CODES (use potential_code with count):
• 17110: Benign destruction 1-14 lesions (0.70 wRVU)
• 17111: Benign destruction 15+ lesions (1.23 wRVU)

═══════════════════════════════════════════════════════════════════════════════
CATEGORY 4: DESTRUCTION - GENITAL LESIONS
═══════════════════════════════════════════════════════════════════════════════
CLINICAL TRIGGERS:
• Condylomata acuminata (genital warts)
• Genital molluscum
• Any benign genital lesion requiring destruction

BILLING CODES (use code_options - mutually exclusive):
MALE:
• 54050: Simple destruction (0.61 wRVU) - 1-2 small lesions
• 54055: Extensive destruction (1.50 wRVU) - multiple or large lesions

FEMALE:
• 56501: Simple destruction (0.70 wRVU)
• 56515: Extensive destruction (1.87 wRVU)

═══════════════════════════════════════════════════════════════════════════════
CATEGORY 5: NAIL PROCEDURES
═══════════════════════════════════════════════════════════════════════════════
CLINICAL TRIGGERS:
• Dystrophic nails (thickened, discolored)
• Onychomycosis
• Subungual debris
• Nail psoriasis with dystrophy

BILLING CODES (use potential_code with count):
• 11720: Nail debridement 1-5 nails (0.34 wRVU)
• 11721: Nail debridement 6+ nails (0.53 wRVU)

═══════════════════════════════════════════════════════════════════════════════
CATEGORY 6: BIOPSY OPPORTUNITIES
═══════════════════════════════════════════════════════════════════════════════
CLINICAL TRIGGERS:
• Any lesion with ABCDE criteria (asymmetry, border, color, diameter, evolution)
• New or changing pigmented lesions
• Non-healing lesions
• Uncertain diagnosis requiring histopathology
• Inflammatory conditions unresponsive to treatment

BILLING CODES (use potential_code):
• 11102: Tangential biopsy - first lesion (0.56 wRVU) - superficial lesions
• 11103: Tangential biopsy - each additional (+0.17 wRVU)
• 11104: Punch biopsy - first lesion (0.69 wRVU) - deeper sampling needed
• 11105: Punch biopsy - each additional (+0.24 wRVU)
• 11106: Incisional biopsy - first lesion (1.01 wRVU) - large/deep lesions
• 11107: Incisional biopsy - each additional (+0.48 wRVU)

═══════════════════════════════════════════════════════════════════════════════
CATEGORY 7: E/M LEVEL OPTIMIZATION
═══════════════════════════════════════════════════════════════════════════════
CRITICAL: DO NOT use code_options for E/M. Determine the SINGLE MAXIMUM REASONABLE
achievable code and use potential_code. Then specify EXACTLY what to document.

E/M LEVELS (Established):
• 99213 (1.30 wRVU): Straightforward MDM - single self-limited condition
• 99214 (1.92 wRVU): Moderate MDM - multiple conditions, Rx management, or chronic illness
• 99215 (2.80 wRVU): High MDM - severe/life-threatening, multiple Rx, or extensive counseling

WHAT SUPPORTS EACH LEVEL:
99214 requires ONE of:
- 2+ chronic conditions requiring management decisions
- Prescription drug requiring monitoring (labs, side effects)
- Condition with mild exacerbation or uncertain diagnosis

99215 requires ONE of:
- Condition with severe exacerbation or significant risk
- Drug requiring intensive monitoring (biologics, immunosuppressants)
- Decision about hospitalization or referral for emergency

USE CLINICAL JUDGMENT:
- Simple acne follow-up → 99213 max (99214 only if multiple conditions or Rx issues)
- Psoriasis on biologic → 99214-99215 (complex management, drug monitoring)
- Eczema with infection → 99214 (acute on chronic)
- Melanoma follow-up with new suspicious lesions → 99215 (high risk)

ADD-ON: G2211 (+0.33 wRVU) for established ongoing care relationship

OUTPUT FORMAT for E/M:
{{"category": "visit_level", "finding": "[What in this note supports higher E/M]",
  "opportunity": "E/M upgrade to 99214", "action": "Document: [EXACTLY what to add]",
  "potential_code": {{"code": "99214-25", "description": "Moderate MDM with procedure", "wRVU": 1.92}},
  "teaching_point": "[Why this level is appropriate and defensible]"}}

═══════════════════════════════════════════════════════════════════════════════
CATEGORY 8: COMORBIDITY CAPTURE
═══════════════════════════════════════════════════════════════════════════════
Look for unaddressed conditions that could warrant separate work:
• Psoriatic arthritis screening in psoriasis patients
• Depression/anxiety screening in chronic skin conditions
• Nail involvement in psoriasis (separate from skin)
• Eye involvement in rosacea

═══════════════════════════════════════════════════════════════════════════════
OUTPUT RULES
═══════════════════════════════════════════════════════════════════════════════

1. ONE OPPORTUNITY CARD PER CODE FAMILY
   - IL injections (11900/11901) = one card with count input
   - AK destruction (17000-17004) = one card with count input
   - Nail debridement (11720/11721) = one card with count input
   - DO NOT mix different code families in one card

2. FOR COUNT-BASED CODES: Use potential_code with estimated count in description
   Example: {{"code": "11900", "description": "IL injection ~4 lesions", "wRVU": 0.52}}

3. FOR TIER-BASED CODES: Use code_options with 2-3 tiers max
   Example: [{{"code": "54050", "description": "Simple", "wRVU": 0.61, "threshold": "1-2 lesions"}},
             {{"code": "54055", "description": "Extensive", "wRVU": 1.50, "threshold": "Multiple/large"}}]

4. TEACHING POINT: Include billing tip or clinical pearl

5. Be SPECIFIC about clinical findings that triggered each opportunity

RESPOND WITH JSON ONLY:
{{"opportunities": [
  {{"category": "procedure|visit_level|comorbidity",
    "finding": "[Specific clinical finding from note]",
    "opportunity": "[What could be billed]",
    "action": "[What provider should do]",
    "potential_code": {{"code": "X", "description": "X", "wRVU": 0.00}},
    "teaching_point": "[Billing tip]"}}
], "optimized_note": "[Full rewritten note with all opportunities documented as performed]",
"total_potential_additional_wRVU": 0.00}}"""

        system = """You are an expert dermatology billing educator and optimizer.

CRITICAL RULES:
1. Only suggest opportunities that are CLINICALLY APPROPRIATE given the patient's presentation
2. ONE card per code family - aggregate related findings (e.g., all injection-worthy lesions in one card)
3. Be SPECIFIC - reference actual findings from the note, not generic suggestions
4. Include accurate wRVU values from the reference
5. Focus on HIGH-VALUE opportunities first (procedures > E/M adjustments)

E/M CRITICAL: For E/M levels, use clinical judgment to determine the MAXIMUM REASONABLE
achievable code - the one that would actually be paid by insurance. DO NOT offer a range
of E/M options. Pick ONE specific level and tell the provider exactly what to document.

USE potential_code (single code) for:
- E/M levels (determine best achievable)
- Count-based procedures (injections, AKs, nails, biopsies)
- Single clear recommendations

USE code_options (2-3 choices) ONLY for:
- Truly mutually exclusive tiers (genital destruction: simple vs extensive)
- Non-count-based procedure choices

OUTPUT: Valid JSON only. No markdown, no explanation outside JSON."""

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

                code_options = None
                if o.get("code_options"):
                    from .models import CodeOption
                    code_options = [
                        CodeOption(
                            code=co["code"],
                            description=co.get("description", ""),
                            wRVU=float(co.get("wRVU", 0)),
                            threshold=co.get("threshold", ""),
                        )
                        for co in o["code_options"]
                    ]

                opportunities.append(FutureOpportunity(
                    category=o["category"],
                    finding=o["finding"],
                    opportunity=o["opportunity"],
                    action=o["action"],
                    potential_code=potential_code,
                    code_options=code_options,
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
        prompt = f"""You are a dermatology billing optimization expert. Analyze this clinical note to identify MISSED billing opportunities - procedures/services that COULD have been performed but WERE NOT.

CLINICAL NOTE:
{note_text}

EXTRACTED ENTITIES:
{json.dumps(entities.model_dump(), indent=2)}

CLINICAL SCENARIO GUIDANCE:
{scenario_content}

BILLING REFERENCE:
{corpus_context}

YOUR TASK: Identify what the provider COULD HAVE DONE during this encounter to increase billing. These are NOT documentation fixes - these are actual clinical interventions that were missed.

═══════════════════════════════════════════════════════════════════════════════
CATEGORY 1: THERAPEUTIC INJECTIONS
═══════════════════════════════════════════════════════════════════════════════
CLINICAL TRIGGERS:
• Thick psoriasis plaques (especially if topicals failing)
• Recalcitrant eczema patches
• Keloids or hypertrophic scars
• Alopecia areata patches
• Lichen simplex chronicus
• Granuloma annulare
• Cystic acne

BILLING CODES (use potential_code with count):
• 11900: IL injection 1-7 lesions (0.52 wRVU)
• 11901: IL injection 8+ lesions (0.82 wRVU)

CRITICAL: Multiple injection sites for DIFFERENT conditions (e.g., psoriasis plaques + keloid)
are SEPARATE opportunities. Each becomes a count input that aggregates in the UI.

Example finding: "Thick psoriasis plaques on elbows not responding to topicals"
Example action: "IL triamcinolone 10mg/mL injection to thick plaques"

═══════════════════════════════════════════════════════════════════════════════
CATEGORY 2: DESTRUCTION - PREMALIGNANT (AKs)
═══════════════════════════════════════════════════════════════════════════════
CLINICAL TRIGGERS:
• Sun-damaged skin in sun-exposed areas
• History of skin cancer
• Actinic keratoses noted on exam
• Rough/scaly patches on face, scalp, arms, hands

BILLING CODES (use potential_code - count-based):
• 17000: First AK destruction (0.61 wRVU)
• 17003: Each additional AK 2-14 (+0.09 wRVU each)
• 17004: 15+ AKs flat rate (2.19 wRVU)

Example: 10 AKs = 17000 + 17003x9 = 0.61 + 0.81 = 1.42 wRVU

═══════════════════════════════════════════════════════════════════════════════
CATEGORY 3: DESTRUCTION - BENIGN LESIONS
═══════════════════════════════════════════════════════════════════════════════
CLINICAL TRIGGERS:
• Seborrheic keratoses (symptomatic - itching, irritation, catching on clothes)
• Skin tags in friction areas
• Verrucae (warts)
• Molluscum contagiosum
• Cherry angiomas (if symptomatic)

BILLING CODES (use potential_code with count):
• 17110: Benign destruction 1-14 lesions (0.70 wRVU)
• 17111: Benign destruction 15+ lesions (1.23 wRVU)

═══════════════════════════════════════════════════════════════════════════════
CATEGORY 4: DESTRUCTION - GENITAL LESIONS
═══════════════════════════════════════════════════════════════════════════════
CLINICAL TRIGGERS:
• Condylomata acuminata (genital warts)
• Genital molluscum
• Any benign genital lesion requiring destruction

BILLING CODES (use code_options - mutually exclusive):
MALE:
• 54050: Simple destruction (0.61 wRVU) - 1-2 small lesions
• 54055: Extensive destruction (1.50 wRVU) - multiple or large lesions

FEMALE:
• 56501: Simple destruction (0.70 wRVU)
• 56515: Extensive destruction (1.87 wRVU)

═══════════════════════════════════════════════════════════════════════════════
CATEGORY 5: NAIL PROCEDURES
═══════════════════════════════════════════════════════════════════════════════
CLINICAL TRIGGERS:
• Dystrophic nails (thickened, discolored)
• Onychomycosis
• Subungual debris
• Nail psoriasis with dystrophy

BILLING CODES (use potential_code with count):
• 11720: Nail debridement 1-5 nails (0.34 wRVU)
• 11721: Nail debridement 6+ nails (0.53 wRVU)

═══════════════════════════════════════════════════════════════════════════════
CATEGORY 6: BIOPSY OPPORTUNITIES
═══════════════════════════════════════════════════════════════════════════════
CLINICAL TRIGGERS:
• Any lesion with ABCDE criteria (asymmetry, border, color, diameter, evolution)
• New or changing pigmented lesions
• Non-healing lesions
• Uncertain diagnosis requiring histopathology
• Inflammatory conditions unresponsive to treatment

BILLING CODES (use potential_code):
• 11102: Tangential biopsy - first lesion (0.56 wRVU) - superficial lesions
• 11103: Tangential biopsy - each additional (+0.17 wRVU)
• 11104: Punch biopsy - first lesion (0.69 wRVU) - deeper sampling needed
• 11105: Punch biopsy - each additional (+0.24 wRVU)
• 11106: Incisional biopsy - first lesion (1.01 wRVU) - large/deep lesions
• 11107: Incisional biopsy - each additional (+0.48 wRVU)

═══════════════════════════════════════════════════════════════════════════════
CATEGORY 7: E/M LEVEL OPTIMIZATION
═══════════════════════════════════════════════════════════════════════════════
CRITICAL: DO NOT use code_options for E/M. Determine the SINGLE MAXIMUM REASONABLE
achievable code and use potential_code. Then specify EXACTLY what to document.

E/M LEVELS (Established):
• 99213 (1.30 wRVU): Straightforward MDM - single self-limited condition
• 99214 (1.92 wRVU): Moderate MDM - multiple conditions, Rx management, or chronic illness
• 99215 (2.80 wRVU): High MDM - severe/life-threatening, multiple Rx, or extensive counseling

WHAT SUPPORTS EACH LEVEL:
99214 requires ONE of:
- 2+ chronic conditions requiring management decisions
- Prescription drug requiring monitoring (labs, side effects)
- Condition with mild exacerbation or uncertain diagnosis

99215 requires ONE of:
- Condition with severe exacerbation or significant risk
- Drug requiring intensive monitoring (biologics, immunosuppressants)
- Decision about hospitalization or referral for emergency

USE CLINICAL JUDGMENT:
- Simple acne follow-up → 99213 max (99214 only if multiple conditions or Rx issues)
- Psoriasis on biologic → 99214-99215 (complex management, drug monitoring)
- Eczema with infection → 99214 (acute on chronic)
- Melanoma follow-up with new suspicious lesions → 99215 (high risk)

ADD-ON: G2211 (+0.33 wRVU) for established ongoing care relationship

OUTPUT FORMAT for E/M:
{{"category": "visit_level", "finding": "[What in this note supports higher E/M]",
  "opportunity": "E/M upgrade to 99214", "action": "Document: [EXACTLY what to add]",
  "potential_code": {{"code": "99214-25", "description": "Moderate MDM with procedure", "wRVU": 1.92}},
  "teaching_point": "[Why this level is appropriate and defensible]"}}

═══════════════════════════════════════════════════════════════════════════════
CATEGORY 8: COMORBIDITY CAPTURE
═══════════════════════════════════════════════════════════════════════════════
Look for unaddressed conditions that could warrant separate work:
• Psoriatic arthritis screening in psoriasis patients
• Depression/anxiety screening in chronic skin conditions
• Nail involvement in psoriasis (separate from skin)
• Eye involvement in rosacea

═══════════════════════════════════════════════════════════════════════════════
OUTPUT RULES
═══════════════════════════════════════════════════════════════════════════════

1. ONE OPPORTUNITY CARD PER CODE FAMILY
   - IL injections (11900/11901) = one card with count input
   - AK destruction (17000-17004) = one card with count input
   - Nail debridement (11720/11721) = one card with count input
   - DO NOT mix different code families in one card

2. FOR COUNT-BASED CODES: Use potential_code with estimated count in description
   Example: {{"code": "11900", "description": "IL injection ~4 lesions", "wRVU": 0.52}}

3. FOR TIER-BASED CODES: Use code_options with 2-3 tiers max
   Example: [{{"code": "54050", "description": "Simple", "wRVU": 0.61, "threshold": "1-2 lesions"}},
             {{"code": "54055", "description": "Extensive", "wRVU": 1.50, "threshold": "Multiple/large"}}]

4. TEACHING POINT: Include billing tip or clinical pearl

5. Be SPECIFIC about clinical findings that triggered each opportunity

RESPOND WITH JSON ONLY:
{{"opportunities": [
  {{"category": "procedure|visit_level|comorbidity",
    "finding": "[Specific clinical finding from note]",
    "opportunity": "[What could be billed]",
    "action": "[What provider should do]",
    "potential_code": {{"code": "X", "description": "X", "wRVU": 0.00}},
    "teaching_point": "[Billing tip]"}}
], "optimized_note": "[Full rewritten note with all opportunities documented as performed]",
"total_potential_additional_wRVU": 0.00}}"""

        system = """You are an expert dermatology billing educator and optimizer.

CRITICAL RULES:
1. Only suggest opportunities that are CLINICALLY APPROPRIATE given the patient's presentation
2. ONE card per code family - aggregate related findings (e.g., all injection-worthy lesions in one card)
3. Be SPECIFIC - reference actual findings from the note, not generic suggestions
4. Include accurate wRVU values from the reference
5. Focus on HIGH-VALUE opportunities first (procedures > E/M adjustments)

E/M CRITICAL: For E/M levels, use clinical judgment to determine the MAXIMUM REASONABLE
achievable code - the one that would actually be paid by insurance. DO NOT offer a range
of E/M options. Pick ONE specific level and tell the provider exactly what to document.

USE potential_code (single code) for:
- E/M levels (determine best achievable)
- Count-based procedures (injections, AKs, nails, biopsies)
- Single clear recommendations

USE code_options (2-3 choices) ONLY for:
- Truly mutually exclusive tiers (genital destruction: simple vs extensive)
- Non-count-based procedure choices

OUTPUT: Valid JSON only. No markdown, no explanation outside JSON."""

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

                code_options = None
                if o.get("code_options"):
                    from .models import CodeOption
                    code_options = [
                        CodeOption(
                            code=co["code"],
                            description=co.get("description", ""),
                            wRVU=float(co.get("wRVU", 0)),
                            threshold=co.get("threshold", ""),
                        )
                        for co in o["code_options"]
                    ]

                opportunities.append(FutureOpportunity(
                    category=o["category"],
                    finding=o["finding"],
                    opportunity=o["opportunity"],
                    action=o["action"],
                    potential_code=potential_code,
                    code_options=code_options,
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
1. PRESERVE THE ORIGINAL NOTE FORMAT - if input has HPI/Physical/Assessment/Plan sections, output must have same structure
2. Write the note AS IF all selected procedures/services were actually performed
3. For opportunities (things that weren't done): document them as if they WERE done
4. For enhancements: add the documentation details that support higher billing
5. The final note should fully support billing all selected codes
6. Keep the note professional and clinically appropriate
7. Output ONLY the complete rewritten note - no explanations

CRITICAL: Match the original note's structure and formatting style exactly.

OUTPUT THE COMPLETE OPTIMIZED NOTE:"""

        system = """Medical documentation expert. Create notes that support maximum billing.

CRITICAL: Preserve the original note's format and structure:
- If input has sections (HPI, Physical Exam, Assessment, Plan), keep those sections
- If input is SOAP format, output SOAP format
- If input is free-text paragraph, output paragraph

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
