"""
Pydantic models for DermBill AI input/output structures.
"""

from typing import Optional
from pydantic import BaseModel, Field


# ============================================================================
# Entity Extraction Models (Step 1)
# ============================================================================

class ExtractedEntity(BaseModel):
    """A single extracted entity from the clinical note."""
    entity_type: str = Field(..., description="Type: diagnosis, procedure, site, measurement, medication, time")
    value: str = Field(..., description="The extracted value")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Confidence score")
    context: Optional[str] = Field(default=None, description="Surrounding context from note")


class ExtractedEntities(BaseModel):
    """All entities extracted from a clinical note."""
    diagnoses: list[str] = Field(default_factory=list, description="Conditions/diagnoses mentioned")
    procedures: list[str] = Field(default_factory=list, description="Procedures performed")
    anatomic_sites: list[str] = Field(default_factory=list, description="Body locations")
    measurements: list[dict] = Field(default_factory=list, description="Sizes, counts, lengths")
    medications: list[str] = Field(default_factory=list, description="Medications prescribed/administered")
    time_documentation: Optional[str] = Field(default=None, description="Time spent if documented")
    raw_entities: list[ExtractedEntity] = Field(default_factory=list, description="All raw extracted entities")


# ============================================================================
# Billing Code Models (Step 2)
# ============================================================================

class BillingCode(BaseModel):
    """A single billable CPT/HCPCS code."""
    code: str = Field(..., description="CPT or HCPCS code")
    modifier: Optional[str] = Field(default=None, description="Modifier if applicable (e.g., -25, -59)")
    description: str = Field(..., description="Code description")
    wRVU: float = Field(..., ge=0.0, description="Work relative value units")
    units: int = Field(default=1, ge=1, description="Number of units")
    status: str = Field(default="supported", description="supported, missing_documentation, or flagged")
    documentation_note: Optional[str] = Field(default=None, description="Note about documentation status")


class CurrentBilling(BaseModel):
    """Current maximum billing from the note as written."""
    codes: list[BillingCode] = Field(default_factory=list, description="All billable codes")
    total_wRVU: float = Field(default=0.0, ge=0.0, description="Total work RVUs")
    documentation_gaps: list[str] = Field(default_factory=list, description="Gaps preventing billing")


# ============================================================================
# Documentation Enhancement Models (Step 3)
# ============================================================================

class DocumentationEnhancement(BaseModel):
    """A single documentation enhancement recommendation."""
    issue: str = Field(..., description="What documentation is missing/incomplete")
    current_code: Optional[str] = Field(default=None, description="Current code if applicable")
    current_wRVU: float = Field(default=0.0, ge=0.0, description="Current wRVU")
    suggested_addition: str = Field(..., description="Specific language to add to note")
    enhanced_code: Optional[str] = Field(default=None, description="Enhanced code after documentation fix")
    enhanced_wRVU: float = Field(default=0.0, ge=0.0, description="Enhanced wRVU")
    delta_wRVU: float = Field(default=0.0, description="Improvement in wRVU")
    priority: str = Field(default="medium", description="high, medium, or low priority")


class DocumentationEnhancements(BaseModel):
    """All documentation enhancement recommendations."""
    enhancements: list[DocumentationEnhancement] = Field(default_factory=list)
    suggested_addendum: Optional[str] = Field(default=None, description="Complete addendum text to add")
    optimized_note: Optional[str] = Field(default=None, description="Full optimized note text (copy-pasteable)")
    enhanced_total_wRVU: float = Field(default=0.0, ge=0.0, description="Total wRVU after enhancements")
    improvement: float = Field(default=0.0, description="Total wRVU improvement")


# ============================================================================
# Future Opportunity Models (Step 4)
# ============================================================================

class PotentialCode(BaseModel):
    """A potential code that could have been billed."""
    code: str = Field(..., description="CPT/HCPCS code")
    description: str = Field(..., description="Code description")
    wRVU: float = Field(..., ge=0.0, description="Work RVUs")


class FutureOpportunity(BaseModel):
    """A single future opportunity ('next time' recommendation)."""
    category: str = Field(..., description="comorbidity, procedure, visit_level, or documentation")
    finding: str = Field(..., description="What was found in the note")
    opportunity: str = Field(..., description="What was missed")
    action: str = Field(..., description="What to do next time")
    potential_code: Optional[PotentialCode] = Field(default=None, description="Code if action taken")
    teaching_point: str = Field(..., description="Educational explanation")


class FutureOpportunities(BaseModel):
    """All future opportunity recommendations."""
    opportunities: list[FutureOpportunity] = Field(default_factory=list)
    optimized_note: Optional[str] = Field(default=None, description="Full optimized note with opportunities (copy-pasteable)")
    total_potential_additional_wRVU: float = Field(default=0.0, ge=0.0, description="Total potential additional wRVU")


# ============================================================================
# Complete Analysis Result
# ============================================================================

class AnalysisResult(BaseModel):
    """Complete analysis result containing all four steps."""
    # Step 1: Entity Extraction
    entities: ExtractedEntities = Field(..., description="Extracted entities from note")

    # Step 2: Current Maximum Billing
    current_billing: CurrentBilling = Field(..., description="What can be billed now")

    # Step 3: Documentation Enhancement
    documentation_enhancements: DocumentationEnhancements = Field(..., description="Documentation improvements")

    # Step 4: Future Opportunities
    future_opportunities: FutureOpportunities = Field(..., description="Next time recommendations")

    # Metadata
    original_note: str = Field(..., description="The original clinical note")
    compliance_notice: str = Field(
        default="These recommendations are for educational purposes and require clinical judgment. "
                "All billing must reflect services actually performed and documented. "
                "This tool identifies optimization opportunities within legitimate coding guidelines. "
                "Consult your compliance officer for facility-specific guidance.",
        description="Required compliance disclaimer"
    )


# ============================================================================
# API Request/Response Models
# ============================================================================

class AnalyzeRequest(BaseModel):
    """Request body for the /analyze endpoint."""
    note: str = Field(..., min_length=10, description="Clinical note text to analyze")


class RegenerateNoteRequest(BaseModel):
    """Request body for the /regenerate-note endpoint."""
    original_note: str = Field(..., description="Original clinical note")
    selected_enhancements: list[dict] = Field(default_factory=list, description="Selected enhancement recommendations")
    selected_opportunities: list[dict] = Field(default_factory=list, description="Selected future opportunities")


class RegenerateNoteResponse(BaseModel):
    """Response for the /regenerate-note endpoint."""
    optimized_note: str = Field(..., description="Regenerated optimized note")
    included_enhancements: int = Field(..., description="Number of enhancements included")
    included_opportunities: int = Field(..., description="Number of opportunities included")


class CodeLookupResponse(BaseModel):
    """Response for code lookup endpoint."""
    code: str
    category: str
    subcategory: Optional[str] = None
    description: str
    detailed_explanation: Optional[str] = None
    anatomic_site: Optional[str] = None
    size_range: Optional[str] = None
    documentation_requirements: Optional[str] = None
    optimization_notes: Optional[str] = None
    wRVU: float
    global_period: Optional[str] = None
    is_addon: bool = False
    related_codes: Optional[str] = None
    modifier_notes: Optional[str] = None


class ScenarioListResponse(BaseModel):
    """Response for listing available scenarios."""
    scenarios: list[str] = Field(..., description="List of available scenario names")


class ScenarioResponse(BaseModel):
    """Response for a single scenario."""
    name: str
    content: str


class HealthResponse(BaseModel):
    """Health check response."""
    status: str = "healthy"
    version: str
    corpus_loaded: bool
