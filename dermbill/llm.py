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
3. Suggest MEDICOLEGAL enhancements for missing safety documentation
4. Flag COUNT-BASED PROCEDURES where count is UNSPECIFIED (critical for billing accuracy)

CRITICAL: If a procedure/exam/service WAS NOT DONE, it belongs in Step 4 (Opportunities), NOT here.

═══════════════════════════════════════════════════════════════════════════════
COUNT EXTRACTION: PRINCIPLE-BASED APPROACH
═══════════════════════════════════════════════════════════════════════════════

CORE PRINCIPLE: A count IS SPECIFIED if the PROCEDURE DESCRIPTION (Plan section)
contains ANY numeric or countable information about what was treated.

EXTRACTION RULES - Apply in order:
1. EXPLICIT NUMBER: Any digit in the procedure text → use that number
   • "injected into 4 thick plaques" → count = 4 ✓
   • "debridement of 3 nails" → count = 3 ✓
   • "treated 6 AKs" → count = 6 ✓

2. ANATOMIC COUNTING: Bilateral/paired anatomy → calculate count
   • "bilateral elbows" → count = 2 ✓
   • "bilateral elbows and knees" → count = 4 (2+2) ✓
   • "both hands" → count = 2 ✓

3. LISTED SITES: Enumerated locations → count the list
   • "injected scalp, left arm, right arm" → count = 3 ✓
   • "treated forehead, nose, and cheeks" → count = 3 ✓

4. ANATOMIC IMPLICATION: Specific anatomy implies count
   • "all 10 toenails debrided" → count = 10 ✓
   • "both great toenails" → count = 2 ✓

CRITICAL: Only use COUNT_CLARIFICATION when the procedure text has NO countable info:
- "Nail debridement performed" → no count anywhere → COUNT_CLARIFICATION
- "IL injection given" → no count anywhere → COUNT_CLARIFICATION
- "AKs treated" → no count anywhere → COUNT_CLARIFICATION

WRONG - Do NOT flag as unspecified if count exists ANYWHERE in procedure text:
- "IL triamcinolone 10mg/mL injected into 4 thick plaques" → count = 4 (NOT unspecified!)
- "Nail debridement bilateral great toenails" → count = 2 (NOT unspecified!)

Count-based procedure families:
• Nail debridement (11720: 1-5, 11721: 6+)
• IL injections (11900: 1-7, 11901: 8+)
• AK destruction (17000: first, 17003: 2-14, 17004: 15+)
• Benign destruction (17110: 1-14, 17111: 15+)

CRITICAL - SITE-SPECIFIC DESTRUCTION CODES:
For genital/anal lesions, NEVER use generic 17110/17111. Use site-specific codes:
• Female genital (vulvar warts, etc): 56501 (simple) or 56515 (extensive)
• Male genital (penile warts, etc): 54050 (simple) or 54055 (extensive)
• Anal/perianal: 46900 (simple) or 46910 (extensive)

When genital/anal destruction is documented WITHOUT "extensive" language, create EXTENSIVE_UPGRADE:
{{"issue": "Vulvar destruction - upgrade to extensive?", "current_code": "56501", "current_wRVU": 0.70,
  "suggested_addition": "Was destruction extensive? If yes, add: 'Extensive destruction performed'",
  "enhanced_code": "56515", "enhanced_wRVU": 1.87, "delta_wRVU": 1.17, "priority": "extensive_upgrade",
  "upgrade_family": "female_genital_destruction", "default_extensive": true}}

EXAM vs PLAN: NEVER use exam counts for billing. If exam says "8 nails dystrophic"
but Plan says "nail debridement performed" with no count → COUNT_CLARIFICATION
(The exam count is what exists; the Plan count is what was treated)

For COUNT_CLARIFICATION cards, use this format in enhancements:
{{"issue": "Nail debridement count unspecified", "current_code": "11720", "current_wRVU": 0.31,
  "suggested_addition": "CLARIFY: How many nails were actually debrided? Enter count to determine correct billing code.",
  "enhanced_code": "COUNT_CLARIFY", "enhanced_wRVU": 0, "delta_wRVU": 0, "priority": "count_clarification",
  "count_family": "nail_debridement", "default_count": 1}}

VALID Step 3 Enhancements (things that WERE done):
- G2211 add-on: Chronic condition relationship EXISTS → document it (+0.33 wRVU)
- G2212 add-on: Prolonged visit (>40min established, >60min new) → document time (+0.61 wRVU)
- E/M upgrade: MDM/counseling DID happen → document complexity to support higher level
- Code upgrades: Repair WAS done → document technique for intermediate vs simple
- Unbundling: Multiple procedures WERE done → separate under different diagnoses
- COUNT_CLARIFICATION: Procedure WAS done but count is ambiguous → ask user to specify

ADD-ON CODES (bill WITH primary codes when applicable):
• Biopsies: 11103/11105/11107 for each additional lesion biopsied
• Skin tags: 11201 (+0.28 wRVU) for each additional 10 tags removed beyond first 15
• Nail avulsion: 11732 (+0.37 wRVU) for each additional nail beyond first
• Complex repairs: 13102/13122/13133/13153 for each additional 5cm repaired
• Tissue transfer: 14302 (+3.64 wRVU) for each additional 30 sq cm
• Full-thickness graft: 15261 (+2.17 wRVU) for each additional graft area

MEDICOLEGAL ENHANCEMENTS (enhanced_code: "LEGAL", delta_wRVU: 0):
CRITICAL PRINCIPLE: Avoid selective risk documentation. If documenting one risk in detail,
document ALL relevant risks - or document none. Selective documentation creates liability:
"You documented skin cancer risk but not infection - why the inconsistency?"

USE SPARINGLY - Only suggest medicolegal enhancement when documentation is:
1. MISSING a critical safety element that was clearly discussed (e.g., follow-up timing)
2. INCOMPLETE for shared decision-making (e.g., "discussed biologics" without any risk mention)

AVOID suggesting medicolegal additions when:
- Risk discussion is ALREADY documented (even briefly) - don't expand selectively
- The note says "risks/benefits discussed" - this is legally sufficient
- Adding would create INCONSISTENT depth (one risk detailed, others brief)

Example: If note says "discussed biologic therapy including risks and benefits" → SUFFICIENT
Do NOT add separate "skin cancer surveillance" documentation unless ALL other risks are equally expanded

INVALID for Step 3 (move to Step 4):
- "Injection not documented" when NO injection was given
- "Exam not performed" → that's a missed opportunity, not an enhancement
- Any procedure that COULD have been done but WASN'T
- Treating MORE lesions/nails than were actually treated (that's Step 4)

JSON format:
{{"current_billing": {{"codes": [{{"code": "X", "modifier": "X", "description": "X", "wRVU": 0, "units": 1, "status": "supported|count_unspecified"}}], "total_wRVU": 0, "documentation_gaps": []}},
"enhancements": [{{"issue": "X", "current_code": "X", "current_wRVU": 0, "suggested_addition": "X", "enhanced_code": "X", "enhanced_wRVU": 0, "delta_wRVU": 0, "priority": "high|medicolegal|count_clarification", "count_family": "optional", "default_count": 1}}],
"suggested_addendum": "X", "optimized_note": "X", "enhanced_total_wRVU": 0, "improvement": 0}}

ENHANCEMENT TYPES - USE THE CORRECT FORMAT:

1. COUNT_CLARIFICATION (count-based procedure done but count not specified):
   {{"issue": "Nail debridement count unspecified", "current_code": "11720", "current_wRVU": 0.31,
     "suggested_addition": "Enter actual count performed", "enhanced_code": "COUNT_CLARIFY",
     "enhanced_wRVU": 0, "delta_wRVU": 0, "priority": "count_clarification",
     "count_family": "nail_debridement", "default_count": 1}}

   COUNT FAMILIES: nail_debridement, il_injection, ak_destruction, benign_destruction

2. MEDICOLEGAL (safety documentation, no wRVU):
   {{"issue": "Missing safety documentation", "current_code": null, "current_wRVU": 0,
     "suggested_addition": "Add: Patient counseled on...", "enhanced_code": "LEGAL",
     "enhanced_wRVU": 0, "delta_wRVU": 0, "priority": "medicolegal"}}

3. BILLING ENHANCEMENT (code upgrade, unbundling, G2211):
   {{"issue": "G2211 chronic care add-on", "current_code": "99214", "current_wRVU": 1.92,
     "suggested_addition": "Add: Ongoing management of chronic condition...",
     "enhanced_code": "99214 + G2211", "enhanced_wRVU": 2.25, "delta_wRVU": 0.33, "priority": "high"}}

4. EXTENSIVE_UPGRADE (genital/anal destruction - simple vs extensive):
   When genital or anal destruction is documented WITHOUT explicit "extensive" language,
   suggest upgrading to extensive with template documentation.

   {{"issue": "Vulvar destruction - upgrade to extensive?", "current_code": "56501", "current_wRVU": 0.70,
     "suggested_addition": "Was destruction extensive? If yes, add: 'Extensive destruction performed - multiple lesions requiring extended treatment time and effort'",
     "enhanced_code": "56515", "enhanced_wRVU": 1.87, "delta_wRVU": 1.17, "priority": "extensive_upgrade",
     "upgrade_family": "female_genital_destruction", "default_extensive": true}}

   UPGRADE FAMILIES and codes:
   • female_genital_destruction: 56501 (0.70) → 56515 (1.87) = +167%
   • male_genital_destruction: 54050 (0.61) → 54055 (1.50) = +146%
   • anal_destruction: 46900 (0.91) → 46910 (1.51) = +66%

   TEMPLATE LANGUAGE for extensive (use in optimized note):
   "Extensive destruction performed - multiple lesions across broad treatment area requiring
   extended provider time and careful technique to complete"

   IMPORTANT: The optimized_note MUST include extensive template language for genital/anal
   destruction BY DEFAULT (since "Yes - Extensive" is the default toggle selection).

CRITICAL: If a procedure was done but count is unspecified, you MUST use priority: "count_clarification"
with count_family and default_count. Do NOT suggest a specific count - let the user input it.

OPTIMIZED NOTE RULES - DOCUMENTATION PRINCIPLES:
- Output ONLY the clinical note text - no Time, Coding, or billing sections
- Be CONCISE and FACTUAL: State what was done briefly
- Include safety-critical items when clinically relevant
- For genital/anal destruction: ALWAYS use extensive language in optimized_note (default selection)"""

        system = """Dermatology billing expert. Maximize billing AND medicolegal protection through DOCUMENTATION.

CRITICAL RULES:
1. Only include enhancements for services that WERE PERFORMED
2. COUNT EXTRACTION - PRINCIPLE-BASED:
   - SCAN the procedure text for ANY numeric/countable info (digits, "bilateral", listed sites)
   - If count info EXISTS → status: "supported" with extracted count
   - ONLY use COUNT_CLARIFICATION when procedure text has NO count info whatsoever
   - NEVER assume count from EXAM findings (exam = what exists, plan = what was treated)
3. SITE-SPECIFIC CODES - CRITICAL:
   - Genital warts/lesions → NEVER use 17110. Use 56501/56515 (female) or 54050/54055 (male)
   - Anal/perianal lesions → NEVER use 17110. Use 46900/46910
   - When site-specific destruction done without "extensive" → priority: "extensive_upgrade"
   - optimized_note MUST use extensive language for genital/anal (default is "Yes - Extensive")
4. Medicolegal documentation gaps → priority: "medicolegal", enhanced_code: "LEGAL"
5. G2211, E/M upgrades, unbundling → priority: "high"

DOCUMENTATION PHILOSOPHY: Minimal yet complete.
- Document the minimum necessary to justify billing codes
- Over-documentation creates malpractice liability
- Safety-critical items → MEDICOLEGAL ENHANCEMENT CARDS

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
                    count_family=e.get("count_family"),
                    default_count=int(e["default_count"]) if e.get("default_count") else None,
                    upgrade_family=e.get("upgrade_family"),
                    default_extensive=e.get("default_extensive"),
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
3. Suggest MEDICOLEGAL enhancements for missing safety documentation
4. Flag COUNT-BASED PROCEDURES where count is UNSPECIFIED (critical for billing accuracy)

CRITICAL: If a procedure/exam/service WAS NOT DONE, it belongs in Step 4 (Opportunities), NOT here.

═══════════════════════════════════════════════════════════════════════════════
COUNT EXTRACTION: PRINCIPLE-BASED APPROACH
═══════════════════════════════════════════════════════════════════════════════

CORE PRINCIPLE: A count IS SPECIFIED if the PROCEDURE DESCRIPTION (Plan section)
contains ANY numeric or countable information about what was treated.

EXTRACTION RULES - Apply in order:
1. EXPLICIT NUMBER: Any digit in the procedure text → use that number
   • "injected into 4 thick plaques" → count = 4 ✓
   • "debridement of 3 nails" → count = 3 ✓
   • "treated 6 AKs" → count = 6 ✓

2. ANATOMIC COUNTING: Bilateral/paired anatomy → calculate count
   • "bilateral elbows" → count = 2 ✓
   • "bilateral elbows and knees" → count = 4 (2+2) ✓
   • "both hands" → count = 2 ✓

3. LISTED SITES: Enumerated locations → count the list
   • "injected scalp, left arm, right arm" → count = 3 ✓
   • "treated forehead, nose, and cheeks" → count = 3 ✓

4. ANATOMIC IMPLICATION: Specific anatomy implies count
   • "all 10 toenails debrided" → count = 10 ✓
   • "both great toenails" → count = 2 ✓

CRITICAL: Only use COUNT_CLARIFICATION when the procedure text has NO countable info:
- "Nail debridement performed" → no count anywhere → COUNT_CLARIFICATION
- "IL injection given" → no count anywhere → COUNT_CLARIFICATION
- "AKs treated" → no count anywhere → COUNT_CLARIFICATION

WRONG - Do NOT flag as unspecified if count exists ANYWHERE in procedure text:
- "IL triamcinolone 10mg/mL injected into 4 thick plaques" → count = 4 (NOT unspecified!)
- "Nail debridement bilateral great toenails" → count = 2 (NOT unspecified!)

Count-based procedure families:
• Nail debridement (11720: 1-5, 11721: 6+)
• IL injections (11900: 1-7, 11901: 8+)
• AK destruction (17000: first, 17003: 2-14, 17004: 15+)
• Benign destruction (17110: 1-14, 17111: 15+)

CRITICAL - SITE-SPECIFIC DESTRUCTION CODES:
For genital/anal lesions, NEVER use generic 17110/17111. Use site-specific codes:
• Female genital (vulvar warts, etc): 56501 (simple) or 56515 (extensive)
• Male genital (penile warts, etc): 54050 (simple) or 54055 (extensive)
• Anal/perianal: 46900 (simple) or 46910 (extensive)

When genital/anal destruction is documented WITHOUT "extensive" language, create EXTENSIVE_UPGRADE:
{{"issue": "Vulvar destruction - upgrade to extensive?", "current_code": "56501", "current_wRVU": 0.70,
  "suggested_addition": "Was destruction extensive? If yes, add: 'Extensive destruction performed'",
  "enhanced_code": "56515", "enhanced_wRVU": 1.87, "delta_wRVU": 1.17, "priority": "extensive_upgrade",
  "upgrade_family": "female_genital_destruction", "default_extensive": true}}

EXAM vs PLAN: NEVER use exam counts for billing. If exam says "8 nails dystrophic"
but Plan says "nail debridement performed" with no count → COUNT_CLARIFICATION
(The exam count is what exists; the Plan count is what was treated)

For COUNT_CLARIFICATION cards, use this format in enhancements:
{{"issue": "Nail debridement count unspecified", "current_code": "11720", "current_wRVU": 0.31,
  "suggested_addition": "CLARIFY: How many nails were actually debrided? Enter count to determine correct billing code.",
  "enhanced_code": "COUNT_CLARIFY", "enhanced_wRVU": 0, "delta_wRVU": 0, "priority": "count_clarification",
  "count_family": "nail_debridement", "default_count": 1}}

VALID Step 3 Enhancements (things that WERE done):
- G2211 add-on: Chronic condition relationship EXISTS → document it (+0.33 wRVU)
- G2212 add-on: Prolonged visit (>40min established, >60min new) → document time (+0.61 wRVU)
- E/M upgrade: MDM/counseling DID happen → document complexity to support higher level
- Code upgrades: Repair WAS done → document technique for intermediate vs simple
- Unbundling: Multiple procedures WERE done → separate under different diagnoses
- COUNT_CLARIFICATION: Procedure WAS done but count is ambiguous → ask user to specify

ADD-ON CODES (bill WITH primary codes when applicable):
• Biopsies: 11103/11105/11107 for each additional lesion biopsied
• Skin tags: 11201 (+0.28 wRVU) for each additional 10 tags removed beyond first 15
• Nail avulsion: 11732 (+0.37 wRVU) for each additional nail beyond first
• Complex repairs: 13102/13122/13133/13153 for each additional 5cm repaired
• Tissue transfer: 14302 (+3.64 wRVU) for each additional 30 sq cm
• Full-thickness graft: 15261 (+2.17 wRVU) for each additional graft area

MEDICOLEGAL ENHANCEMENTS (enhanced_code: "LEGAL", delta_wRVU: 0):
CRITICAL PRINCIPLE: Avoid selective risk documentation. If documenting one risk in detail,
document ALL relevant risks - or document none. Selective documentation creates liability:
"You documented skin cancer risk but not infection - why the inconsistency?"

USE SPARINGLY - Only suggest medicolegal enhancement when documentation is:
1. MISSING a critical safety element that was clearly discussed (e.g., follow-up timing)
2. INCOMPLETE for shared decision-making (e.g., "discussed biologics" without any risk mention)

AVOID suggesting medicolegal additions when:
- Risk discussion is ALREADY documented (even briefly) - don't expand selectively
- The note says "risks/benefits discussed" - this is legally sufficient
- Adding would create INCONSISTENT depth (one risk detailed, others brief)

Example: If note says "discussed biologic therapy including risks and benefits" → SUFFICIENT
Do NOT add separate "skin cancer surveillance" documentation unless ALL other risks are equally expanded

INVALID for Step 3 (move to Step 4):
- "Injection not documented" when NO injection was given
- "Exam not performed" → that's a missed opportunity, not an enhancement
- Any procedure that COULD have been done but WASN'T
- Treating MORE lesions/nails than were actually treated (that's Step 4)

JSON format:
{{"current_billing": {{"codes": [{{"code": "X", "modifier": "X", "description": "X", "wRVU": 0, "units": 1, "status": "supported|count_unspecified"}}], "total_wRVU": 0, "documentation_gaps": []}},
"enhancements": [{{"issue": "X", "current_code": "X", "current_wRVU": 0, "suggested_addition": "X", "enhanced_code": "X", "enhanced_wRVU": 0, "delta_wRVU": 0, "priority": "high|medicolegal|count_clarification", "count_family": "optional", "default_count": 1}}],
"suggested_addendum": "X", "optimized_note": "X", "enhanced_total_wRVU": 0, "improvement": 0}}

ENHANCEMENT TYPES - USE THE CORRECT FORMAT:

1. COUNT_CLARIFICATION (count-based procedure done but count not specified):
   {{"issue": "Nail debridement count unspecified", "current_code": "11720", "current_wRVU": 0.31,
     "suggested_addition": "Enter actual count performed", "enhanced_code": "COUNT_CLARIFY",
     "enhanced_wRVU": 0, "delta_wRVU": 0, "priority": "count_clarification",
     "count_family": "nail_debridement", "default_count": 1}}

   COUNT FAMILIES: nail_debridement, il_injection, ak_destruction, benign_destruction

2. MEDICOLEGAL (safety documentation, no wRVU):
   {{"issue": "Missing safety documentation", "current_code": null, "current_wRVU": 0,
     "suggested_addition": "Add: Patient counseled on...", "enhanced_code": "LEGAL",
     "enhanced_wRVU": 0, "delta_wRVU": 0, "priority": "medicolegal"}}

3. BILLING ENHANCEMENT (code upgrade, unbundling, G2211):
   {{"issue": "G2211 chronic care add-on", "current_code": "99214", "current_wRVU": 1.92,
     "suggested_addition": "Add: Ongoing management of chronic condition...",
     "enhanced_code": "99214 + G2211", "enhanced_wRVU": 2.25, "delta_wRVU": 0.33, "priority": "high"}}

4. EXTENSIVE_UPGRADE (genital/anal destruction - simple vs extensive):
   When genital or anal destruction is documented WITHOUT explicit "extensive" language,
   suggest upgrading to extensive with template documentation.

   {{"issue": "Vulvar destruction - upgrade to extensive?", "current_code": "56501", "current_wRVU": 0.70,
     "suggested_addition": "Was destruction extensive? If yes, add: 'Extensive destruction performed - multiple lesions requiring extended treatment time and effort'",
     "enhanced_code": "56515", "enhanced_wRVU": 1.87, "delta_wRVU": 1.17, "priority": "extensive_upgrade",
     "upgrade_family": "female_genital_destruction", "default_extensive": true}}

   UPGRADE FAMILIES and codes:
   • female_genital_destruction: 56501 (0.70) → 56515 (1.87) = +167%
   • male_genital_destruction: 54050 (0.61) → 54055 (1.50) = +146%
   • anal_destruction: 46900 (0.91) → 46910 (1.51) = +66%

   TEMPLATE LANGUAGE for extensive (use in optimized note):
   "Extensive destruction performed - multiple lesions across broad treatment area requiring
   extended provider time and careful technique to complete"

   IMPORTANT: The optimized_note MUST include extensive template language for genital/anal
   destruction BY DEFAULT (since "Yes - Extensive" is the default toggle selection).

CRITICAL: If a procedure was done but count is unspecified, you MUST use priority: "count_clarification"
with count_family and default_count. Do NOT suggest a specific count - let the user input it.

OPTIMIZED NOTE RULES - DOCUMENTATION PRINCIPLES:
- Output ONLY the clinical note text - no Time, Coding, or billing sections
- Be CONCISE and FACTUAL: State what was done briefly
- Include safety-critical items when clinically relevant
- For genital/anal destruction: ALWAYS use extensive language in optimized_note (default selection)"""

        system = """Dermatology billing expert. Maximize billing AND medicolegal protection through DOCUMENTATION.

CRITICAL RULES:
1. Only include enhancements for services that WERE PERFORMED
2. COUNT EXTRACTION - PRINCIPLE-BASED:
   - SCAN the procedure text for ANY numeric/countable info (digits, "bilateral", listed sites)
   - If count info EXISTS → status: "supported" with extracted count
   - ONLY use COUNT_CLARIFICATION when procedure text has NO count info whatsoever
   - NEVER assume count from EXAM findings (exam = what exists, plan = what was treated)
3. SITE-SPECIFIC CODES - CRITICAL:
   - Genital warts/lesions → NEVER use 17110. Use 56501/56515 (female) or 54050/54055 (male)
   - Anal/perianal lesions → NEVER use 17110. Use 46900/46910
   - When site-specific destruction done without "extensive" → priority: "extensive_upgrade"
   - optimized_note MUST use extensive language for genital/anal (default is "Yes - Extensive")
4. Medicolegal documentation gaps → priority: "medicolegal", enhanced_code: "LEGAL"
5. G2211, E/M upgrades, unbundling → priority: "high"

DOCUMENTATION PHILOSOPHY: Minimal yet complete.
- Document the minimum necessary to justify billing codes
- Over-documentation creates malpractice liability
- Safety-critical items → MEDICOLEGAL ENHANCEMENT CARDS

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
                    count_family=e.get("count_family"),
                    default_count=int(e["default_count"]) if e.get("default_count") else None,
                    upgrade_family=e.get("upgrade_family"),
                    default_extensive=e.get("default_extensive"),
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

YOUR TASK: MAXIMIZE RVU by identifying ALL opportunities to increase billing through:

1. UPGRADES (check EVERY count-based procedure in Plan):
   A. UNDERTREATMENT: Fewer sites treated than exam shows exist
      → "4 plaques injected" but exam shows 8 → opportunity to treat all 8
   B. AMBIGUOUS COUNT: No count documented → only baseline tier billable
      → "nail debridement performed" (no count) but exam shows 8 dystrophic nails
      → opportunity: document/treat all 8 to bill 11721 instead of 11720
   C. SUBOPTIMAL TIER: Count specified but below tier threshold
      → "5 AKs treated" → just 1 more gets 17003x5 instead of 17003x4

2. ADDITIONS: Procedures NOT done that clinical findings clearly support
   → Thick plaques noted but no injection given → opportunity for IL injection

═══════════════════════════════════════════════════════════════════════════════
CORE PRINCIPLES - RVU MAXIMIZATION
═══════════════════════════════════════════════════════════════════════════════
1. UPGRADE PRINCIPLE: For EVERY count-based procedure in Plan:
   - Extract count from procedure text (use principle-based extraction)
   - Compare to exam findings - are there more treatable sites?
   - Check tier thresholds - could treating more bump to next tier?
   - If upgrade possible → create opportunity card with SPECIFIC target count

2. COUNT EXTRACTION: Use same principles as enhancements:
   - Explicit numbers, bilateral (=2), listed sites, anatomic implications
   - If count IS specified: check for upgrade potential against exam
   - If count NOT specified: this IS an upgrade opportunity (ambiguous → baseline only)

3. TIER THRESHOLD AWARENESS:
   - Nail: 1-5 (11720) → 6+ (11721) [threshold: 6]
   - IL injection: 1-7 (11900) → 8+ (11901) [threshold: 8]
   - Benign: 1-14 (17110) → 15+ (17111) [threshold: 15]
   - AK: first (17000) + 2-14 (17003) → 15+ (17004) [threshold: 15]

4. CLINICAL APPROPRIATENESS: Only suggest what's medically reasonable.

5. ONE CARD PER CODE FAMILY: Aggregate related opportunities.

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
CATEGORY 4: DESTRUCTION - SPECIAL SITES (Genital, Anal, Perianal)
═══════════════════════════════════════════════════════════════════════════════
CRITICAL: Special anatomic sites have SITE-SPECIFIC codes that pay MORE than generic
benign destruction codes (17110/17111). ALWAYS use site-specific codes when applicable.

CLINICAL TRIGGERS:
• Condylomata acuminata (genital/anal warts)
• Genital/anal molluscum
• Perianal skin tags
• Any benign lesion in genital, anal, or perianal region

SIMPLE vs EXTENSIVE DISTINCTION (judgment call - document to justify):
• SIMPLE: Straightforward destruction, minimal complexity
• EXTENSIVE: Choose when ANY of the following apply and DOCUMENT why:
  - Multiple lesions requiring additional time/effort
  - Large or confluent lesions
  - Difficult anatomic location requiring careful technique
  - Multiple treatment modalities needed
  - Significant patient discomfort management
  - Extensive surface area involvement

DOCUMENTATION TIP: To justify extensive, note factors like "multiple confluent lesions",
"required extended treatment time due to [reason]", "difficult anatomic access", etc.

BILLING CODES BY SITE (use code_options - simple vs extensive):

MALE GENITAL:
• 54050: Simple destruction (0.61 wRVU) - 1-2 small lesions
• 54055: Extensive destruction (1.50 wRVU) - multiple/large lesions
• 54056: Cryosurgery penile lesions (1.26 wRVU) - cryo specifically
• 54057: Laser destruction penile lesions (1.50 wRVU) - laser specifically

FEMALE GENITAL (simple vs extensive is JUDGMENT CALL - NOT count-based):
• 56501: Simple vulvar destruction (0.70 wRVU) - DEFAULT when note lacks extensive documentation
• 56515: Extensive vulvar destruction (1.87 wRVU) - requires documentation of extensive effort/time/technique
NOTE: Always bill 56501 in current billing. Create EXTENSIVE_UPGRADE enhancement to let user toggle to 56515.

ANAL/PERIANAL (simple vs extensive is JUDGMENT CALL - NOT count-based):
• 46900: Simple anal destruction (0.91 wRVU) - DEFAULT when note lacks extensive documentation
• 46910: Extensive anal destruction (1.51 wRVU) - requires documentation of extensive effort/time/technique
NOTE: Always bill simple code in current billing. Create EXTENSIVE_UPGRADE enhancement to let user toggle.
• 46916: Cryosurgery anal lesions (1.86 wRVU) - cryo specifically
• 46917: Laser destruction anal lesions (1.95 wRVU) - laser specifically

TIP: If using cryotherapy on anal or penile lesions, use the cryo-specific codes
(46916, 54056) which often pay more than generic simple/extensive codes.

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

USE CLINICAL JUDGMENT - PICK ONE CODE, NEVER A RANGE:
- Simple acne follow-up → 99213 (single condition, stable)
- Acne with multiple components (inflammatory + PIH + cystic) → 99214 (multiple treatment decisions)
- Psoriasis on biologic → 99214 (drug monitoring qualifies as moderate)
- Psoriasis flare requiring biologic switch → 99215 (high-risk drug decision)
- Eczema with secondary infection → 99214 (acute on chronic)
- Melanoma follow-up with new suspicious lesion → 99215 (high risk)

NEVER output "99214-99215" or any range. Pick the SINGLE highest defensible code.

ADD-ONS:
• G2211 (+0.33 wRVU): Established ongoing care relationship (chronic condition management)
• G2212 (+0.61 wRVU): Prolonged visit - document total face-to-face time >40min est/60min new

OUTPUT FORMAT for E/M:
{{"category": "visit_level", "finding": "[What in this note supports higher E/M]",
  "opportunity": "E/M upgrade to 99214", "action": "Document: [EXACTLY what to add]",
  "potential_code": {{"code": "99214", "description": "Moderate MDM (add -25 mod if billing with procedure)", "wRVU": 1.92}},
  "teaching_point": "[Why this level is appropriate and defensible]"}}

NOTE: Output just the E/M code (99213, 99214, 99215) without the modifier.
The -25 modifier is added when billing E/M same-day with a procedure - mention this in description.

═══════════════════════════════════════════════════════════════════════════════
CATEGORY 8: PROCEDURE UPGRADES
═══════════════════════════════════════════════════════════════════════════════
PRINCIPLE: When a procedure was performed but more treatable sites exist, suggest
treating all to reach higher billing tiers.

TIER THRESHOLDS:
• Nail debridement: 6+ nails → 11721 (vs 11720 for 1-5)
• IL injections: 8+ lesions → 11901 (vs 11900 for 1-7)
• AK destruction: 15+ → 17004 flat rate (vs 17000+17003)
• Benign destruction: 15+ → 17111 (vs 17110 for 1-14)

EXAMPLES:
• Psoriasis with nail dystrophy: If nails debrided but 8+ show dystrophy → upgrade opportunity
• Thick plaques injected: If 4 plaques injected but 6 total present → upgrade to 8+ tier
• Multiple AKs: If 10 treated but 20 visible → suggest treating all for 17004

═══════════════════════════════════════════════════════════════════════════════
CATEGORY 9: COMORBIDITY CAPTURE
═══════════════════════════════════════════════════════════════════════════════
Look for unaddressed conditions that could warrant separate work:
• Psoriatic arthritis screening in psoriasis patients
• Depression/anxiety screening in chronic skin conditions
• Nail involvement in psoriasis (separate from skin)
• Eye involvement in rosacea

═══════════════════════════════════════════════════════════════════════════════
CATEGORY 10: MEDICOLEGAL DOCUMENTATION (no wRVU but critical for liability)
═══════════════════════════════════════════════════════════════════════════════
CRITICAL PRINCIPLE: Avoid selective risk documentation. Either document ALL relevant
risks or none - inconsistent depth creates liability ("Why skin cancer but not infection?")

Only suggest medicolegal additions when:
• Follow-up timing is COMPLETELY missing (not just brief)
• Risk discussion is ABSENT when high-risk treatment discussed
• Clinical reasoning needed for atypical lesion NOT biopsied

DO NOT suggest when:
• "Risks and benefits discussed" already present - legally sufficient
• Expanding one risk would create inconsistent documentation depth

For medicolegal opportunities, use:
- category: "medicolegal"
- potential_code: {{"code": "LEGAL", "description": "[What to document]", "wRVU": 0}}

═══════════════════════════════════════════════════════════════════════════════
CATEGORY 11: DOCUMENTATION-DRIVEN UPGRADES (same work, higher billing)
═══════════════════════════════════════════════════════════════════════════════
These are HIGH-VALUE opportunities where the clinical work was already done,
but documentation upgrades justify higher-paying codes. Look for these actively!

1. REPAIR TYPE UPGRADES (Simple → Intermediate → Complex):
   • Simple (12001-12021): Single layer closure
   • Intermediate (12031-12057): Document "layered closure" or "extensive undermining"
   • Complex (13100-13160): Document debridement, scar revision, stents, retention sutures

   DOCUMENTATION TIP: If you closed in layers, document it! "Layered closure performed"
   upgrades simple to intermediate. "Extensive undermining required" also qualifies.
   wRVU difference: Simple 2.5cm trunk (12001) = 1.11 vs Intermediate (12031) = 1.95

2. EXCISION SIZE DOCUMENTATION:
   • Excision codes are tiered by size: lesion diameter + 2x narrowest margin
   • Document the TOTAL size including margins, not just lesion size
   • Example: 0.8cm lesion with 0.3cm margins = 1.4cm total → higher tier code

   DOCUMENTATION TIP: "Excision performed, specimen measured 1.4 cm including margins"

3. BIOPSY TYPE SELECTION (all clinically equivalent for many lesions):
   • 11102 Tangential (0.56 wRVU): superficial sampling
   • 11104 Punch (0.69 wRVU): full-thickness sampling
   • 11106 Incisional (1.01 wRVU): large/deep lesions, partial removal

   DOCUMENTATION TIP: If punch was used, document "punch biopsy". If you excised
   a portion of a larger lesion, that's incisional (1.01 vs 0.56 wRVU).

4. DESTRUCTION MODALITY DOCUMENTATION:
   • Cryo-specific codes (46916, 54056) often pay more than generic codes
   • Document the specific modality: "cryotherapy", "electrodesiccation", "laser"

5. WOUND COMPLEXITY FACTORS (justify intermediate/complex):
   • Contaminated wound requiring debridement
   • Wound edges requiring trimming/revision
   • Undermining performed for tension-free closure
   • Bleeding requiring more than simple pressure
   • Location requiring meticulous technique (eyelid, lip vermillion)

6. E/M COMPLEXITY DOCUMENTATION:
   • Reviewing outside records → document "reviewed external records from [source]"
   • Discussing with other providers → document the consultation
   • Extended counseling → document time and topics discussed
   • Multiple diagnoses → list each one addressed

For documentation upgrades, use category: "documentation_upgrade"
{{"category": "documentation_upgrade", "finding": "[What was done]",
  "opportunity": "[Higher code available]", "action": "Document: [specific language]",
  "potential_code": {{"code": "12031", "description": "Intermediate repair - document layered closure", "wRVU": 1.95}},
  "teaching_point": "[Why this documentation justifies the upgrade]"}}

═══════════════════════════════════════════════════════════════════════════════
OUTPUT RULES
═══════════════════════════════════════════════════════════════════════════════
1. ONE CARD PER CODE FAMILY: Don't mix IL injections with AK destruction, etc.

2. COUNT-BASED CODES: Include specific count extracted from note anatomy in description
   (e.g., "IL injection ~4 lesions", "Nail debridement 8 nails")

3. TIER-BASED CODES: Use code_options array for codes with discrete tiers

4. TEACHING POINT: Brief billing tip explaining why this opportunity exists

5. CLINICAL SPECIFICITY: Reference actual findings from the note

RESPOND WITH JSON ONLY:
{{"opportunities": [
  {{"category": "procedure|visit_level|comorbidity|medicolegal|documentation_upgrade",
    "finding": "[Specific clinical finding from note]",
    "opportunity": "[What could be billed or documented]",
    "action": "[What provider should do]",
    "potential_code": {{"code": "X", "description": "X", "wRVU": 0.00}},
    "teaching_point": "[Billing tip or medicolegal rationale]"}}
], "optimized_note": "[Full rewritten note with all opportunities documented as performed]",
"total_potential_additional_wRVU": 0.00}}

OPTIMIZED NOTE RULES:
- Output ONLY the clinical note text - no Time, Coding, or billing sections
- Be CONCISE and FACTUAL
- Include safety documentation when clinically relevant"""

        system = """You are an expert dermatology billing educator and optimizer. MAXIMIZE RVU.

CRITICAL RULES - RVU MAXIMIZATION:
1. CHECK EVERY count-based procedure in Plan for upgrade potential:
   - Extract count using principle-based approach (digits, bilateral, listed sites)
   - Compare to exam findings - more treatable sites = upgrade opportunity
   - Ambiguous count (no count in procedure) = upgrade opportunity (baseline tier only billable)
2. ONE card per code family - aggregate related findings
3. Be SPECIFIC - reference actual findings, target counts, tier thresholds
4. Include accurate wRVU values from reference
5. HIGH-VALUE first (procedures > E/M)
6. ONE medicolegal card if safety documentation missing

UPGRADE DETECTION CHECKLIST:
□ Nail debridement done? → Check: exam nail count vs treated count → suggest 6+ for 11721
□ IL injection done? → Check: exam lesion count vs injected count → suggest 8+ for 11901
□ AK destruction done? → Check: exam AK count vs treated count → suggest 15+ for 17004
□ Benign destruction done? → Check: exam lesion count vs treated count → suggest 15+ for 17111
□ Count ambiguous in any above? → This IS an upgrade opportunity (baseline only billable)

E/M CRITICAL: Pick ONE specific code, not a range. No -25 in code field.

USE potential_code (single code) for:
- E/M levels, count-based procedures, medicolegal items

USE code_options ONLY for:
- Truly mutually exclusive tiers (genital: simple vs extensive)

OUTPUT: Valid JSON only."""

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

YOUR TASK: MAXIMIZE RVU by identifying ALL opportunities to increase billing through:

1. UPGRADES (check EVERY count-based procedure in Plan):
   A. UNDERTREATMENT: Fewer sites treated than exam shows exist
      → "4 plaques injected" but exam shows 8 → opportunity to treat all 8
   B. AMBIGUOUS COUNT: No count documented → only baseline tier billable
      → "nail debridement performed" (no count) but exam shows 8 dystrophic nails
      → opportunity: document/treat all 8 to bill 11721 instead of 11720
   C. SUBOPTIMAL TIER: Count specified but below tier threshold
      → "5 AKs treated" → just 1 more gets 17003x5 instead of 17003x4

2. ADDITIONS: Procedures NOT done that clinical findings clearly support
   → Thick plaques noted but no injection given → opportunity for IL injection

═══════════════════════════════════════════════════════════════════════════════
CORE PRINCIPLES - RVU MAXIMIZATION
═══════════════════════════════════════════════════════════════════════════════
1. UPGRADE PRINCIPLE: For EVERY count-based procedure in Plan:
   - Extract count from procedure text (use principle-based extraction)
   - Compare to exam findings - are there more treatable sites?
   - Check tier thresholds - could treating more bump to next tier?
   - If upgrade possible → create opportunity card with SPECIFIC target count

2. COUNT EXTRACTION: Use same principles as enhancements:
   - Explicit numbers, bilateral (=2), listed sites, anatomic implications
   - If count IS specified: check for upgrade potential against exam
   - If count NOT specified: this IS an upgrade opportunity (ambiguous → baseline only)

3. TIER THRESHOLD AWARENESS:
   - Nail: 1-5 (11720) → 6+ (11721) [threshold: 6]
   - IL injection: 1-7 (11900) → 8+ (11901) [threshold: 8]
   - Benign: 1-14 (17110) → 15+ (17111) [threshold: 15]
   - AK: first (17000) + 2-14 (17003) → 15+ (17004) [threshold: 15]

4. CLINICAL APPROPRIATENESS: Only suggest what's medically reasonable.

5. ONE CARD PER CODE FAMILY: Aggregate related opportunities.

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
CATEGORY 4: DESTRUCTION - SPECIAL SITES (Genital, Anal, Perianal)
═══════════════════════════════════════════════════════════════════════════════
CRITICAL: Special anatomic sites have SITE-SPECIFIC codes that pay MORE than generic
benign destruction codes (17110/17111). ALWAYS use site-specific codes when applicable.

CLINICAL TRIGGERS:
• Condylomata acuminata (genital/anal warts)
• Genital/anal molluscum
• Perianal skin tags
• Any benign lesion in genital, anal, or perianal region

SIMPLE vs EXTENSIVE DISTINCTION (judgment call - document to justify):
• SIMPLE: Straightforward destruction, minimal complexity
• EXTENSIVE: Choose when ANY of the following apply and DOCUMENT why:
  - Multiple lesions requiring additional time/effort
  - Large or confluent lesions
  - Difficult anatomic location requiring careful technique
  - Multiple treatment modalities needed
  - Significant patient discomfort management
  - Extensive surface area involvement

DOCUMENTATION TIP: To justify extensive, note factors like "multiple confluent lesions",
"required extended treatment time due to [reason]", "difficult anatomic access", etc.

BILLING CODES BY SITE (use code_options - simple vs extensive):

MALE GENITAL:
• 54050: Simple destruction (0.61 wRVU) - 1-2 small lesions
• 54055: Extensive destruction (1.50 wRVU) - multiple/large lesions
• 54056: Cryosurgery penile lesions (1.26 wRVU) - cryo specifically
• 54057: Laser destruction penile lesions (1.50 wRVU) - laser specifically

FEMALE GENITAL (simple vs extensive is JUDGMENT CALL - NOT count-based):
• 56501: Simple vulvar destruction (0.70 wRVU) - DEFAULT when note lacks extensive documentation
• 56515: Extensive vulvar destruction (1.87 wRVU) - requires documentation of extensive effort/time/technique
NOTE: Always bill 56501 in current billing. Create EXTENSIVE_UPGRADE enhancement to let user toggle to 56515.

ANAL/PERIANAL (simple vs extensive is JUDGMENT CALL - NOT count-based):
• 46900: Simple anal destruction (0.91 wRVU) - DEFAULT when note lacks extensive documentation
• 46910: Extensive anal destruction (1.51 wRVU) - requires documentation of extensive effort/time/technique
NOTE: Always bill simple code in current billing. Create EXTENSIVE_UPGRADE enhancement to let user toggle.
• 46916: Cryosurgery anal lesions (1.86 wRVU) - cryo specifically
• 46917: Laser destruction anal lesions (1.95 wRVU) - laser specifically

TIP: If using cryotherapy on anal or penile lesions, use the cryo-specific codes
(46916, 54056) which often pay more than generic simple/extensive codes.

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

USE CLINICAL JUDGMENT - PICK ONE CODE, NEVER A RANGE:
- Simple acne follow-up → 99213 (single condition, stable)
- Acne with multiple components (inflammatory + PIH + cystic) → 99214 (multiple treatment decisions)
- Psoriasis on biologic → 99214 (drug monitoring qualifies as moderate)
- Psoriasis flare requiring biologic switch → 99215 (high-risk drug decision)
- Eczema with secondary infection → 99214 (acute on chronic)
- Melanoma follow-up with new suspicious lesion → 99215 (high risk)

NEVER output "99214-99215" or any range. Pick the SINGLE highest defensible code.

ADD-ONS:
• G2211 (+0.33 wRVU): Established ongoing care relationship (chronic condition management)
• G2212 (+0.61 wRVU): Prolonged visit - document total face-to-face time >40min est/60min new

OUTPUT FORMAT for E/M:
{{"category": "visit_level", "finding": "[What in this note supports higher E/M]",
  "opportunity": "E/M upgrade to 99214", "action": "Document: [EXACTLY what to add]",
  "potential_code": {{"code": "99214", "description": "Moderate MDM (add -25 mod if billing with procedure)", "wRVU": 1.92}},
  "teaching_point": "[Why this level is appropriate and defensible]"}}

NOTE: Output just the E/M code (99213, 99214, 99215) without the modifier.
The -25 modifier is added when billing E/M same-day with a procedure - mention this in description.

═══════════════════════════════════════════════════════════════════════════════
CATEGORY 8: PROCEDURE UPGRADES
═══════════════════════════════════════════════════════════════════════════════
PRINCIPLE: When a procedure was performed but more treatable sites exist, suggest
treating all to reach higher billing tiers.

TIER THRESHOLDS:
• Nail debridement: 6+ nails → 11721 (vs 11720 for 1-5)
• IL injections: 8+ lesions → 11901 (vs 11900 for 1-7)
• AK destruction: 15+ → 17004 flat rate (vs 17000+17003)
• Benign destruction: 15+ → 17111 (vs 17110 for 1-14)

EXAMPLES:
• Psoriasis with nail dystrophy: If nails debrided but 8+ show dystrophy → upgrade opportunity
• Thick plaques injected: If 4 plaques injected but 6 total present → upgrade to 8+ tier
• Multiple AKs: If 10 treated but 20 visible → suggest treating all for 17004

═══════════════════════════════════════════════════════════════════════════════
CATEGORY 9: COMORBIDITY CAPTURE
═══════════════════════════════════════════════════════════════════════════════
Look for unaddressed conditions that could warrant separate work:
• Psoriatic arthritis screening in psoriasis patients
• Depression/anxiety screening in chronic skin conditions
• Nail involvement in psoriasis (separate from skin)
• Eye involvement in rosacea

═══════════════════════════════════════════════════════════════════════════════
CATEGORY 10: MEDICOLEGAL DOCUMENTATION (no wRVU but critical for liability)
═══════════════════════════════════════════════════════════════════════════════
CRITICAL PRINCIPLE: Avoid selective risk documentation. Either document ALL relevant
risks or none - inconsistent depth creates liability ("Why skin cancer but not infection?")

Only suggest medicolegal additions when:
• Follow-up timing is COMPLETELY missing (not just brief)
• Risk discussion is ABSENT when high-risk treatment discussed
• Clinical reasoning needed for atypical lesion NOT biopsied

DO NOT suggest when:
• "Risks and benefits discussed" already present - legally sufficient
• Expanding one risk would create inconsistent documentation depth

For medicolegal opportunities, use:
- category: "medicolegal"
- potential_code: {{"code": "LEGAL", "description": "[What to document]", "wRVU": 0}}

═══════════════════════════════════════════════════════════════════════════════
CATEGORY 11: DOCUMENTATION-DRIVEN UPGRADES (same work, higher billing)
═══════════════════════════════════════════════════════════════════════════════
These are HIGH-VALUE opportunities where the clinical work was already done,
but documentation upgrades justify higher-paying codes. Look for these actively!

1. REPAIR TYPE UPGRADES (Simple → Intermediate → Complex):
   • Simple (12001-12021): Single layer closure
   • Intermediate (12031-12057): Document "layered closure" or "extensive undermining"
   • Complex (13100-13160): Document debridement, scar revision, stents, retention sutures

   DOCUMENTATION TIP: If you closed in layers, document it! "Layered closure performed"
   upgrades simple to intermediate. "Extensive undermining required" also qualifies.
   wRVU difference: Simple 2.5cm trunk (12001) = 1.11 vs Intermediate (12031) = 1.95

2. EXCISION SIZE DOCUMENTATION:
   • Excision codes are tiered by size: lesion diameter + 2x narrowest margin
   • Document the TOTAL size including margins, not just lesion size
   • Example: 0.8cm lesion with 0.3cm margins = 1.4cm total → higher tier code

   DOCUMENTATION TIP: "Excision performed, specimen measured 1.4 cm including margins"

3. BIOPSY TYPE SELECTION (all clinically equivalent for many lesions):
   • 11102 Tangential (0.56 wRVU): superficial sampling
   • 11104 Punch (0.69 wRVU): full-thickness sampling
   • 11106 Incisional (1.01 wRVU): large/deep lesions, partial removal

   DOCUMENTATION TIP: If punch was used, document "punch biopsy". If you excised
   a portion of a larger lesion, that's incisional (1.01 vs 0.56 wRVU).

4. DESTRUCTION MODALITY DOCUMENTATION:
   • Cryo-specific codes (46916, 54056) often pay more than generic codes
   • Document the specific modality: "cryotherapy", "electrodesiccation", "laser"

5. WOUND COMPLEXITY FACTORS (justify intermediate/complex):
   • Contaminated wound requiring debridement
   • Wound edges requiring trimming/revision
   • Undermining performed for tension-free closure
   • Bleeding requiring more than simple pressure
   • Location requiring meticulous technique (eyelid, lip vermillion)

6. E/M COMPLEXITY DOCUMENTATION:
   • Reviewing outside records → document "reviewed external records from [source]"
   • Discussing with other providers → document the consultation
   • Extended counseling → document time and topics discussed
   • Multiple diagnoses → list each one addressed

For documentation upgrades, use category: "documentation_upgrade"
{{"category": "documentation_upgrade", "finding": "[What was done]",
  "opportunity": "[Higher code available]", "action": "Document: [specific language]",
  "potential_code": {{"code": "12031", "description": "Intermediate repair - document layered closure", "wRVU": 1.95}},
  "teaching_point": "[Why this documentation justifies the upgrade]"}}

═══════════════════════════════════════════════════════════════════════════════
OUTPUT RULES
═══════════════════════════════════════════════════════════════════════════════
1. ONE CARD PER CODE FAMILY: Don't mix IL injections with AK destruction, etc.

2. COUNT-BASED CODES: Include specific count extracted from note anatomy in description
   (e.g., "IL injection ~4 lesions", "Nail debridement 8 nails")

3. TIER-BASED CODES: Use code_options array for codes with discrete tiers

4. TEACHING POINT: Brief billing tip explaining why this opportunity exists

5. CLINICAL SPECIFICITY: Reference actual findings from the note

RESPOND WITH JSON ONLY:
{{"opportunities": [
  {{"category": "procedure|visit_level|comorbidity|medicolegal|documentation_upgrade",
    "finding": "[Specific clinical finding from note]",
    "opportunity": "[What could be billed or documented]",
    "action": "[What provider should do]",
    "potential_code": {{"code": "X", "description": "X", "wRVU": 0.00}},
    "teaching_point": "[Billing tip or medicolegal rationale]"}}
], "optimized_note": "[Full rewritten note with all opportunities documented as performed]",
"total_potential_additional_wRVU": 0.00}}

OPTIMIZED NOTE RULES:
- Output ONLY the clinical note text - no Time, Coding, or billing sections
- Be CONCISE and FACTUAL
- Include safety documentation when clinically relevant"""

        system = """You are an expert dermatology billing educator and optimizer. MAXIMIZE RVU.

CRITICAL RULES - RVU MAXIMIZATION:
1. CHECK EVERY count-based procedure in Plan for upgrade potential:
   - Extract count using principle-based approach (digits, bilateral, listed sites)
   - Compare to exam findings - more treatable sites = upgrade opportunity
   - Ambiguous count (no count in procedure) = upgrade opportunity (baseline tier only billable)
2. ONE card per code family - aggregate related findings
3. Be SPECIFIC - reference actual findings, target counts, tier thresholds
4. Include accurate wRVU values from reference
5. HIGH-VALUE first (procedures > E/M)
6. ONE medicolegal card if safety documentation missing

UPGRADE DETECTION CHECKLIST:
□ Nail debridement done? → Check: exam nail count vs treated count → suggest 6+ for 11721
□ IL injection done? → Check: exam lesion count vs injected count → suggest 8+ for 11901
□ AK destruction done? → Check: exam AK count vs treated count → suggest 15+ for 17004
□ Benign destruction done? → Check: exam lesion count vs treated count → suggest 15+ for 17111
□ Count ambiguous in any above? → This IS an upgrade opportunity (baseline only billable)

E/M CRITICAL: Pick ONE specific code, not a range. No -25 in code field.

USE potential_code (single code) for:
- E/M levels, count-based procedures, medicolegal items

USE code_options ONLY for:
- Truly mutually exclusive tiers (genital: simple vs extensive)

OUTPUT: Valid JSON only."""

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
6. Output ONLY the complete rewritten note - no explanations
7. Do NOT include "Time:" or face-to-face time sections
8. Do NOT include "Coding:" or billing code sections - just clinical documentation

CRITICAL - NEVER HALLUCINATE NUMBERS:
- NEVER invent specific counts (lesion counts, measurements, quantities) not in the original note
- If original says "vulvar warts" with no count, do NOT add "8 vulvar warts" - keep it vague
- If original says "multiple lesions", do NOT change to specific numbers
- For extensive destruction: use QUALITATIVE language, not fabricated counts
  GOOD: "Extensive cryotherapy performed to vulvar condylomata requiring extended treatment time"
  BAD: "Cryotherapy to 8 vulvar condylomata" (if 8 wasn't in original)
- Only include specific numbers that appear in the ORIGINAL NOTE or were EXPLICITLY provided
- Fabricating numbers is a MAJOR LIABILITY - physicians can be sued for falsified documentation

MEDICOLEGAL DOCUMENTATION PRINCIPLES - CRITICAL:
- MINIMAL NECESSARY: Document the MINIMUM required to support billing codes
- LESS IS MORE: Excessive detail creates litigation risk - every word can be cross-examined
- Be CONCISE: Brief, factual statements only - no elaborate descriptions
- AVOID: Speculative language, unnecessary adjectives, redundant details, flowery prose

HOWEVER - ALWAYS DOCUMENT SAFETY-CRITICAL ITEMS (failure to document = failure to do):
- Suspicious lesions noted and clinical reasoning (biopsy performed OR why deferred)
- Patient counseling on warning signs (ABCDE changes, non-healing lesions)
- Patient refusal of recommended biopsy/treatment (informed refusal)
- Instructions for follow-up and when to return sooner
- Skin cancer risk factors acknowledged in high-risk patients

BALANCE: Minimal on routine details, thorough on safety documentation.

Match the original note's structure and formatting style exactly.
OUTPUT ONLY CLINICAL DOCUMENTATION - no time, no coding, no billing codes.

OUTPUT THE COMPLETE OPTIMIZED NOTE:"""

        system = """Medical documentation expert. Create minimal, defensible notes that support billing.

ABSOLUTE RULE - NEVER HALLUCINATE NUMBERS:
- NEVER invent counts, measurements, or quantities not in the original note
- If original has no count, keep description vague (e.g., "vulvar condylomata" not "8 vulvar condylomata")
- For extensive procedures, use QUALITATIVE language: "extensive treatment", "multiple lesions", "broad area"
- Fabricating specific numbers is MEDICAL FRAUD and creates massive liability

CRITICAL: Preserve the original note's format and structure:
- If input has sections (HPI, Physical Exam, Assessment, Plan), keep those sections
- If input is SOAP format, output SOAP format
- If input is free-text paragraph, output paragraph

Write the note AS IF all selected items were actually performed during the visit.
- If an injection opportunity is selected, document that the injection WAS done
- If an E/M upgrade is selected, document the MDM complexity that supports it
- The note should be copy-paste ready to support billing all selected codes

MEDICOLEGAL DOCUMENTATION PHILOSOPHY:
- Document the MINIMUM NECESSARY to justify each billing code
- Over-documentation creates malpractice liability - every detail can be cross-examined by attorneys
- Concise, factual notes are legally safer than verbose, detailed ones
- BUT: Always document safety-critical items (suspicious lesions, patient counseling, refusals, follow-up)
- "If it wasn't documented, it wasn't done" - this applies to safety items especially
- Use standard terminology, brief statements, objective findings

NEVER include Time, Coding, or billing code sections. Output only pure clinical documentation.
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
