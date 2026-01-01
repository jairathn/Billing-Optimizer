"""
CPT Code Lookup Module

Loads and queries the CPT_Master_Reference.xlsx file for code lookups,
wRVU calculations, and optimization notes.
"""

import os
from pathlib import Path
from typing import Optional
from functools import lru_cache

import pandas as pd

from .models import CodeLookupResponse


class CPTCodeDatabase:
    """Database for CPT/HCPCS code lookups."""

    def __init__(self, excel_path: Optional[str] = None):
        """
        Initialize the CPT code database.

        Args:
            excel_path: Path to CPT_Master_Reference.xlsx. If None, uses default location.
        """
        if excel_path is None:
            # Default to the corpus location relative to this file
            base_dir = Path(__file__).parent.parent
            excel_path = base_dir / "CPT_Master_Reference.xlsx"

        self.excel_path = Path(excel_path)
        self._codes_df: Optional[pd.DataFrame] = None
        self._modifiers_df: Optional[pd.DataFrame] = None
        self._categories_df: Optional[pd.DataFrame] = None
        self._loaded = False

    def load(self) -> None:
        """Load all sheets from the Excel file."""
        if self._loaded:
            return

        if not self.excel_path.exists():
            raise FileNotFoundError(f"CPT reference file not found: {self.excel_path}")

        xlsx = pd.ExcelFile(self.excel_path)

        # Load CPT codes
        self._codes_df = pd.read_excel(xlsx, sheet_name="CPT_Codes")
        self._codes_df["Code"] = self._codes_df["Code"].astype(str)

        # Load modifiers
        self._modifiers_df = pd.read_excel(xlsx, sheet_name="Modifiers")
        self._modifiers_df["Modifier"] = self._modifiers_df["Modifier"].astype(str)

        # Load category index
        self._categories_df = pd.read_excel(xlsx, sheet_name="Category_Index")

        self._loaded = True

    @property
    def codes_df(self) -> pd.DataFrame:
        """Get the codes dataframe, loading if necessary."""
        if not self._loaded:
            self.load()
        return self._codes_df

    @property
    def modifiers_df(self) -> pd.DataFrame:
        """Get the modifiers dataframe, loading if necessary."""
        if not self._loaded:
            self.load()
        return self._modifiers_df

    @property
    def categories_df(self) -> pd.DataFrame:
        """Get the categories dataframe, loading if necessary."""
        if not self._loaded:
            self.load()
        return self._categories_df

    def get_code(self, code: str) -> Optional[CodeLookupResponse]:
        """
        Look up a single CPT/HCPCS code.

        Args:
            code: The code to look up (e.g., "99214", "11102")

        Returns:
            CodeLookupResponse or None if not found
        """
        code = str(code).strip()
        df = self.codes_df

        match = df[df["Code"] == code]
        if match.empty:
            return None

        row = match.iloc[0]

        return CodeLookupResponse(
            code=str(row["Code"]),
            category=str(row["Category"]) if pd.notna(row["Category"]) else "",
            subcategory=str(row["Subcategory"]) if pd.notna(row.get("Subcategory")) else None,
            description=str(row["Official_Description"]) if pd.notna(row["Official_Description"]) else "",
            detailed_explanation=str(row["Detailed_Explanation"]) if pd.notna(row.get("Detailed_Explanation")) else None,
            anatomic_site=str(row["Anatomic_Site"]) if pd.notna(row.get("Anatomic_Site")) else None,
            size_range=str(row["Size_Range"]) if pd.notna(row.get("Size_Range")) else None,
            documentation_requirements=str(row["Documentation_Requirements"]) if pd.notna(row.get("Documentation_Requirements")) else None,
            optimization_notes=str(row["Optimization_Notes"]) if pd.notna(row.get("Optimization_Notes")) else None,
            wRVU=float(row["wRVU"]) if pd.notna(row["wRVU"]) else 0.0,
            global_period=str(row["Global_Period"]) if pd.notna(row.get("Global_Period")) else None,
            is_addon=str(row.get("Add_On_Code", "No")).lower() == "yes",
            related_codes=str(row["Related_Codes"]) if pd.notna(row.get("Related_Codes")) else None,
            modifier_notes=str(row["Modifier_Notes"]) if pd.notna(row.get("Modifier_Notes")) else None,
        )

    def get_wRVU(self, code: str) -> float:
        """
        Get the wRVU for a code.

        Args:
            code: The CPT/HCPCS code

        Returns:
            wRVU value or 0.0 if not found
        """
        code_info = self.get_code(code)
        return code_info.wRVU if code_info else 0.0

    def search_codes(
        self,
        category: Optional[str] = None,
        subcategory: Optional[str] = None,
        anatomic_site: Optional[str] = None,
        keyword: Optional[str] = None,
        min_wRVU: Optional[float] = None,
        max_wRVU: Optional[float] = None,
    ) -> list[CodeLookupResponse]:
        """
        Search for codes matching criteria.

        Args:
            category: Filter by category
            subcategory: Filter by subcategory
            anatomic_site: Filter by anatomic site
            keyword: Search in description
            min_wRVU: Minimum wRVU
            max_wRVU: Maximum wRVU

        Returns:
            List of matching codes
        """
        df = self.codes_df.copy()

        if category:
            df = df[df["Category"].str.contains(category, case=False, na=False)]

        if subcategory:
            df = df[df["Subcategory"].str.contains(subcategory, case=False, na=False)]

        if anatomic_site:
            df = df[df["Anatomic_Site"].str.contains(anatomic_site, case=False, na=False)]

        if keyword:
            df = df[
                df["Official_Description"].str.contains(keyword, case=False, na=False) |
                df["Detailed_Explanation"].str.contains(keyword, case=False, na=False)
            ]

        if min_wRVU is not None:
            df = df[df["wRVU"] >= min_wRVU]

        if max_wRVU is not None:
            df = df[df["wRVU"] <= max_wRVU]

        results = []
        for _, row in df.iterrows():
            results.append(self.get_code(str(row["Code"])))

        return [r for r in results if r is not None]

    def get_codes_by_category(self, category: str) -> list[CodeLookupResponse]:
        """Get all codes in a category."""
        return self.search_codes(category=category)

    def get_modifier(self, modifier: str) -> Optional[dict]:
        """
        Look up a modifier.

        Args:
            modifier: The modifier code (e.g., "25", "59")

        Returns:
            Modifier information dict or None
        """
        modifier = str(modifier).strip().lstrip("-")
        df = self.modifiers_df

        match = df[df["Modifier"] == modifier]
        if match.empty:
            return None

        row = match.iloc[0]
        return {
            "modifier": str(row["Modifier"]),
            "name": str(row["Name"]) if pd.notna(row["Name"]) else "",
            "definition": str(row["Definition"]) if pd.notna(row["Definition"]) else "",
            "when_to_use": str(row["When_To_Use"]) if pd.notna(row["When_To_Use"]) else "",
            "when_not_to_use": str(row["When_NOT_To_Use"]) if pd.notna(row["When_NOT_To_Use"]) else "",
            "derm_examples": str(row["Derm_Examples"]) if pd.notna(row["Derm_Examples"]) else "",
            "revenue_impact": str(row["Revenue_Impact"]) if pd.notna(row["Revenue_Impact"]) else "",
            "audit_risk": str(row["Audit_Risk"]) if pd.notna(row["Audit_Risk"]) else "",
        }

    def get_all_modifiers(self) -> list[dict]:
        """Get all modifiers."""
        self.load()
        results = []
        for _, row in self.modifiers_df.iterrows():
            mod = self.get_modifier(str(row["Modifier"]))
            if mod:
                results.append(mod)
        return results

    def get_category_info(self, category: str) -> Optional[dict]:
        """
        Get category optimization information.

        Args:
            category: The category name

        Returns:
            Category info dict or None
        """
        df = self.categories_df

        match = df[df["Category"].str.contains(category, case=False, na=False)]
        if match.empty:
            return None

        row = match.iloc[0]
        return {
            "category": str(row["Category"]),
            "description": str(row["Description"]) if pd.notna(row["Description"]) else "",
            "code_range": str(row["Code_Range"]) if pd.notna(row["Code_Range"]) else "",
            "key_optimization_points": str(row["Key_Optimization_Points"]) if pd.notna(row["Key_Optimization_Points"]) else "",
        }

    def get_all_categories(self) -> list[dict]:
        """Get all categories with their info."""
        self.load()
        results = []
        for _, row in self.categories_df.iterrows():
            results.append({
                "category": str(row["Category"]),
                "description": str(row["Description"]) if pd.notna(row["Description"]) else "",
                "code_range": str(row["Code_Range"]) if pd.notna(row["Code_Range"]) else "",
                "key_optimization_points": str(row["Key_Optimization_Points"]) if pd.notna(row["Key_Optimization_Points"]) else "",
            })
        return results

    def calculate_total_wRVU(self, codes: list[tuple[str, int, Optional[str]]]) -> float:
        """
        Calculate total wRVU for a list of codes.

        Args:
            codes: List of (code, units, modifier) tuples

        Returns:
            Total wRVU
        """
        total = 0.0
        for code, units, modifier in codes:
            base_wRVU = self.get_wRVU(code)
            # Apply modifier adjustments
            if modifier == "50":  # Bilateral - typically 1.5x
                base_wRVU *= 1.5
            total += base_wRVU * units
        return round(total, 2)

    def is_addon_code(self, code: str) -> bool:
        """Check if a code is an add-on code."""
        code_info = self.get_code(code)
        return code_info.is_addon if code_info else False

    def get_related_codes(self, code: str) -> list[str]:
        """Get related codes for a given code."""
        code_info = self.get_code(code)
        if not code_info or not code_info.related_codes:
            return []
        return [c.strip() for c in code_info.related_codes.split(",")]


# Global instance for convenience
_db_instance: Optional[CPTCodeDatabase] = None


def get_code_database() -> CPTCodeDatabase:
    """Get the global CPT code database instance."""
    global _db_instance
    if _db_instance is None:
        _db_instance = CPTCodeDatabase()
    return _db_instance


# Convenience functions
def lookup_code(code: str) -> Optional[CodeLookupResponse]:
    """Look up a single code."""
    return get_code_database().get_code(code)


def get_wRVU(code: str) -> float:
    """Get wRVU for a code."""
    return get_code_database().get_wRVU(code)


def lookup_modifier(modifier: str) -> Optional[dict]:
    """Look up a modifier."""
    return get_code_database().get_modifier(modifier)
