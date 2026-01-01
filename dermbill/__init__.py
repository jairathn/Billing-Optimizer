"""
DermBill AI: Dermatology Billing Optimization System

An LLM-powered billing optimization tool for dermatologists that analyzes
clinical notes and provides actionable recommendations to capture legitimate
revenue through proper documentation and coding.
"""

__version__ = "1.0.0"

from .analyzer import DermBillAnalyzer
from .models import AnalysisResult, BillingCode, DocumentationEnhancement, FutureOpportunity

__all__ = [
    "DermBillAnalyzer",
    "AnalysisResult",
    "BillingCode",
    "DocumentationEnhancement",
    "FutureOpportunity",
]
