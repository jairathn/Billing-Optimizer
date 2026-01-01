"""
FastAPI REST API Server for DermBill AI

Endpoints:
    POST /analyze - Analyze a clinical note
    GET /codes/{code} - Look up a CPT/HCPCS code
    GET /scenarios - List available scenarios
    GET /scenarios/{name} - Get a specific scenario
    GET /health - Health check
"""

import os
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from .models import (
    AnalyzeRequest,
    AnalysisResult,
    CodeLookupResponse,
    ScenarioListResponse,
    ScenarioResponse,
    HealthResponse,
)
from .analyzer import DermBillAnalyzer
from .codes import get_code_database
from .scenarios import get_scenario_matcher
from . import __version__


# Load environment variables
load_dotenv()

# Global analyzer instance (lazy loaded)
_analyzer: Optional[DermBillAnalyzer] = None


def get_analyzer() -> DermBillAnalyzer:
    """Get or create the analyzer instance."""
    global _analyzer
    if _analyzer is None:
        _analyzer = DermBillAnalyzer()
    return _analyzer


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup/shutdown."""
    # Startup: Pre-load the code database for faster first requests
    try:
        db = get_code_database()
        db.load()
    except Exception:
        pass  # Don't fail startup if corpus isn't available
    yield
    # Shutdown: cleanup if needed
    pass


# Create FastAPI app
app = FastAPI(
    title="DermBill AI",
    description="Dermatology Billing Optimization API - Analyze clinical notes for billing optimization",
    version=__version__,
    lifespan=lifespan,
)

# Add CORS middleware for web clients
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", include_in_schema=False)
async def root():
    """Root endpoint redirect to docs."""
    return {
        "name": "DermBill AI",
        "version": __version__,
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health_check():
    """Health check endpoint."""
    try:
        db = get_code_database()
        db.load()
        corpus_loaded = True
    except Exception:
        corpus_loaded = False

    return HealthResponse(
        status="healthy",
        version=__version__,
        corpus_loaded=corpus_loaded,
    )


@app.post("/analyze", response_model=AnalysisResult, tags=["Analysis"])
async def analyze_note(request: AnalyzeRequest):
    """
    Analyze a clinical note for billing optimization.

    Returns a complete analysis including:
    - Extracted entities
    - Current maximum billing
    - Documentation enhancements
    - Future opportunities
    """
    try:
        analyzer = get_analyzer()
        result = analyzer.analyze(request.note)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis error: {str(e)}")


@app.get("/codes/{code}", response_model=CodeLookupResponse, tags=["Reference"])
async def lookup_code(code: str):
    """
    Look up a CPT/HCPCS code.

    Returns code details including description, wRVU, documentation requirements,
    and optimization notes.
    """
    db = get_code_database()
    result = db.get_code(code)

    if result is None:
        raise HTTPException(status_code=404, detail=f"Code not found: {code}")

    return result


@app.get("/codes", tags=["Reference"])
async def search_codes(
    category: Optional[str] = Query(None, description="Filter by category"),
    keyword: Optional[str] = Query(None, description="Search in description"),
    min_wRVU: Optional[float] = Query(None, description="Minimum wRVU"),
    max_wRVU: Optional[float] = Query(None, description="Maximum wRVU"),
    limit: int = Query(50, ge=1, le=200, description="Maximum results"),
):
    """
    Search CPT/HCPCS codes.

    Filter by category, keyword, or wRVU range.
    """
    db = get_code_database()
    results = db.search_codes(
        category=category,
        keyword=keyword,
        min_wRVU=min_wRVU,
        max_wRVU=max_wRVU,
    )

    return {"codes": [r.model_dump() for r in results[:limit]], "total": len(results)}


@app.get("/modifiers", tags=["Reference"])
async def list_modifiers():
    """List all available modifiers with usage guidance."""
    db = get_code_database()
    modifiers = db.get_all_modifiers()
    return {"modifiers": modifiers}


@app.get("/modifiers/{modifier}", tags=["Reference"])
async def get_modifier(modifier: str):
    """Get details for a specific modifier."""
    db = get_code_database()
    result = db.get_modifier(modifier)

    if result is None:
        raise HTTPException(status_code=404, detail=f"Modifier not found: {modifier}")

    return result


@app.get("/categories", tags=["Reference"])
async def list_categories():
    """List all code categories with optimization points."""
    db = get_code_database()
    categories = db.get_all_categories()
    return {"categories": categories}


@app.get("/scenarios", response_model=ScenarioListResponse, tags=["Scenarios"])
async def list_scenarios():
    """List all available clinical scenarios."""
    matcher = get_scenario_matcher()
    scenarios = matcher.list_scenarios()
    return ScenarioListResponse(scenarios=scenarios)


@app.get("/scenarios/{name}", response_model=ScenarioResponse, tags=["Scenarios"])
async def get_scenario(name: str):
    """
    Get a specific clinical scenario.

    Scenarios provide condition-specific billing optimization guidance.
    """
    matcher = get_scenario_matcher()
    content = matcher.get_scenario_content(name)

    if content is None:
        raise HTTPException(status_code=404, detail=f"Scenario not found: {name}")

    return ScenarioResponse(name=name, content=content)


# Vercel serverless handler
# This is the entry point for Vercel's Python runtime
handler = app
