"""
Command-Line Interface for DermBill AI

Usage:
    python -m dermbill.cli analyze <note_file>
    python -m dermbill.cli analyze -  # Read from stdin
    python -m dermbill.cli code <cpt_code>
    python -m dermbill.cli scenarios
    python -m dermbill.cli scenario <name>
"""

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv


def cmd_analyze(args):
    """Analyze a clinical note."""
    from .analyzer import DermBillAnalyzer

    # Read note text
    if args.note_file == "-":
        note_text = sys.stdin.read()
    else:
        note_path = Path(args.note_file)
        if not note_path.exists():
            print(f"Error: File not found: {args.note_file}", file=sys.stderr)
            sys.exit(1)
        note_text = note_path.read_text(encoding="utf-8")

    if not note_text.strip():
        print("Error: Empty note provided", file=sys.stderr)
        sys.exit(1)

    # Analyze
    print("Analyzing clinical note...", file=sys.stderr)
    analyzer = DermBillAnalyzer()
    result = analyzer.analyze(note_text)

    # Output
    if args.format == "json":
        print(json.dumps(result.model_dump(), indent=2))
    else:
        print_analysis_report(result)


def print_analysis_report(result):
    """Print a human-readable analysis report."""
    print("=" * 70)
    print("DERMBILL AI - BILLING OPTIMIZATION REPORT")
    print("=" * 70)
    print()

    # Step 1: Entities
    print("STEP 1: EXTRACTED ENTITIES")
    print("-" * 40)
    entities = result.entities
    if entities.diagnoses:
        print(f"Diagnoses: {', '.join(entities.diagnoses)}")
    if entities.procedures:
        print(f"Procedures: {', '.join(entities.procedures)}")
    if entities.anatomic_sites:
        print(f"Sites: {', '.join(entities.anatomic_sites)}")
    if entities.measurements:
        print(f"Measurements: {len(entities.measurements)} found")
    if entities.medications:
        print(f"Medications: {', '.join(entities.medications)}")
    print()

    # Step 2: Current Billing
    print("STEP 2: CURRENT MAXIMUM BILLING")
    print("-" * 40)
    current = result.current_billing
    if current.codes:
        print(f"{'Code':<10} {'Mod':<5} {'Description':<35} {'wRVU':>8} {'Status':<15}")
        print("-" * 75)
        for code in current.codes:
            mod = code.modifier or ""
            desc = code.description[:35] if code.description else ""
            print(f"{code.code:<10} {mod:<5} {desc:<35} {code.wRVU:>8.2f} {code.status:<15}")
        print("-" * 75)
        print(f"{'TOTAL wRVU:':<52} {current.total_wRVU:>8.2f}")
    else:
        print("No billable codes identified from current documentation.")

    if current.documentation_gaps:
        print()
        print("Documentation Gaps:")
        for gap in current.documentation_gaps:
            print(f"  - {gap}")
    print()

    # Step 3: Enhancements
    print("STEP 3: DOCUMENTATION ENHANCEMENTS")
    print("-" * 40)
    enhancements = result.documentation_enhancements
    if enhancements.enhancements:
        for i, enh in enumerate(enhancements.enhancements, 1):
            print(f"{i}. [{enh.priority.upper()}] {enh.issue}")
            if enh.current_code:
                print(f"   Current: {enh.current_code} ({enh.current_wRVU:.2f} wRVU)")
            if enh.enhanced_code:
                print(f"   Enhanced: {enh.enhanced_code} ({enh.enhanced_wRVU:.2f} wRVU)")
            print(f"   Add: \"{enh.suggested_addition}\"")
            print(f"   Delta: +{enh.delta_wRVU:.2f} wRVU")
            print()

        if enhancements.suggested_addendum:
            print("SUGGESTED ADDENDUM:")
            print("-" * 40)
            print(enhancements.suggested_addendum)
            print()

        print(f"Enhanced Total wRVU: {enhancements.enhanced_total_wRVU:.2f}")
        print(f"Improvement: +{enhancements.improvement:.2f} wRVU")
    else:
        print("No documentation enhancements identified.")
    print()

    # Step 4: Future Opportunities
    print("STEP 4: FUTURE OPPORTUNITIES ('NEXT TIME')")
    print("-" * 40)
    opportunities = result.future_opportunities
    if opportunities.opportunities:
        for i, opp in enumerate(opportunities.opportunities, 1):
            print(f"{i}. [{opp.category.upper()}] {opp.finding}")
            print(f"   Opportunity: {opp.opportunity}")
            print(f"   Action: {opp.action}")
            if opp.potential_code:
                print(f"   Potential: {opp.potential_code.code} - {opp.potential_code.description} ({opp.potential_code.wRVU:.2f} wRVU)")
            print(f"   Teaching: {opp.teaching_point}")
            print()

        print(f"Total Potential Additional wRVU: {opportunities.total_potential_additional_wRVU:.2f}")
    else:
        print("No future opportunities identified.")
    print()

    # Optimized Notes
    if enhancements.optimized_note:
        print("=" * 70)
        print("OPTIMIZED NOTE (Documentation Enhancements Applied)")
        print("=" * 70)
        print(enhancements.optimized_note)
        print()

    if opportunities.optimized_note:
        print("=" * 70)
        print("OPTIMIZED NOTE (All Opportunities Captured)")
        print("=" * 70)
        print(opportunities.optimized_note)
        print()

    # Compliance notice
    print("=" * 70)
    print("COMPLIANCE NOTICE")
    print("=" * 70)
    print(result.compliance_notice)


def cmd_code(args):
    """Look up a CPT code."""
    from .codes import get_code_database

    db = get_code_database()
    code_info = db.get_code(args.code)

    if code_info is None:
        print(f"Code not found: {args.code}", file=sys.stderr)
        sys.exit(1)

    if args.format == "json":
        print(json.dumps(code_info.model_dump(), indent=2))
    else:
        print(f"Code: {code_info.code}")
        print(f"Category: {code_info.category}")
        if code_info.subcategory:
            print(f"Subcategory: {code_info.subcategory}")
        print(f"Description: {code_info.description}")
        if code_info.detailed_explanation:
            print(f"Explanation: {code_info.detailed_explanation}")
        print(f"wRVU: {code_info.wRVU}")
        if code_info.anatomic_site:
            print(f"Anatomic Site: {code_info.anatomic_site}")
        if code_info.size_range:
            print(f"Size Range: {code_info.size_range}")
        if code_info.documentation_requirements:
            print(f"Documentation: {code_info.documentation_requirements}")
        if code_info.optimization_notes:
            print(f"Optimization: {code_info.optimization_notes}")
        if code_info.is_addon:
            print("Note: This is an add-on code")
        if code_info.related_codes:
            print(f"Related Codes: {code_info.related_codes}")


def cmd_scenarios(args):
    """List available scenarios."""
    from .scenarios import get_scenario_matcher

    matcher = get_scenario_matcher()
    scenarios = matcher.list_scenarios()

    if args.format == "json":
        print(json.dumps({"scenarios": scenarios}, indent=2))
    else:
        print("Available Scenarios:")
        print("-" * 40)
        for scenario in scenarios:
            print(f"  - {scenario}")


def cmd_scenario(args):
    """Get a specific scenario."""
    from .scenarios import get_scenario_matcher

    matcher = get_scenario_matcher()
    content = matcher.get_scenario_content(args.name)

    if content is None:
        print(f"Scenario not found: {args.name}", file=sys.stderr)
        print("Use 'scenarios' command to list available scenarios.", file=sys.stderr)
        sys.exit(1)

    if args.format == "json":
        print(json.dumps({"name": args.name, "content": content}, indent=2))
    else:
        print(content)


def main():
    """Main CLI entry point."""
    # Load environment variables
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="DermBill AI - Dermatology Billing Optimization",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Analyze a clinical note:
    python -m dermbill.cli analyze note.txt
    cat note.txt | python -m dermbill.cli analyze -

  Look up a CPT code:
    python -m dermbill.cli code 99214
    python -m dermbill.cli code 11102 --format json

  List scenarios:
    python -m dermbill.cli scenarios

  Get a scenario:
    python -m dermbill.cli scenario Psoriasis
""",
    )

    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Analyze command
    analyze_parser = subparsers.add_parser("analyze", help="Analyze a clinical note")
    analyze_parser.add_argument(
        "note_file",
        help="Path to clinical note file, or '-' for stdin",
    )
    analyze_parser.set_defaults(func=cmd_analyze)

    # Code lookup command
    code_parser = subparsers.add_parser("code", help="Look up a CPT/HCPCS code")
    code_parser.add_argument("code", help="CPT or HCPCS code to look up")
    code_parser.set_defaults(func=cmd_code)

    # Scenarios list command
    scenarios_parser = subparsers.add_parser("scenarios", help="List available scenarios")
    scenarios_parser.set_defaults(func=cmd_scenarios)

    # Scenario get command
    scenario_parser = subparsers.add_parser("scenario", help="Get a specific scenario")
    scenario_parser.add_argument("name", help="Scenario name")
    scenario_parser.set_defaults(func=cmd_scenario)

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
