"""
Reference tables: CARC/RARC meanings, dermatology code attributes, payer normalization.

CARC and RARC texts are abbreviated from the X12 code lists
(https://x12.org/codes). Keep them short; the dashboard shows them as hints.
"""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

# --------------------------------------------------------------------------- CARC
CARC: dict[str, str] = {
    "1": "Deductible", "2": "Coinsurance", "3": "Copay", "4": "Procedure code inconsistent with modifier",
    "5": "Procedure inconsistent with place of service", "6": "Procedure inconsistent with patient age",
    "11": "Diagnosis inconsistent with procedure", "15": "Authorization number missing/invalid",
    "16": "Claim lacks information or has billing error (see RARC)", "18": "Exact duplicate claim/service",
    "22": "Coordination of benefits: may be covered by another payer", "23": "Prior payer adjudication impact",
    "24": "Charges covered under capitation/managed care", "26": "Expenses incurred prior to coverage",
    "27": "Expenses incurred after coverage terminated", "29": "Time limit for filing has expired",
    "31": "Patient cannot be identified as insured", "35": "Lifetime benefit maximum reached",
    "39": "Services denied at the time authorization was requested", "45": "Charge exceeds fee schedule/maximum allowable",
    "49": "Routine/preventive service not covered", "50": "Not deemed a medical necessity by payer",
    "51": "Pre-existing condition", "54": "Multiple physicians/assistants not covered",
    "55": "Procedure/treatment deemed experimental/investigational", "56": "Procedure not proven effective",
    "58": "Treatment deemed rendered in inappropriate setting", "59": "Multiple/concurrent procedure rules applied",
    "96": "Non-covered charge(s) (see RARC)", "97": "Benefit included in another service already adjudicated",
    "100": "Payment made to patient/insured", "104": "Managed care withholding", "107": "Related/qualifying claim not identified",
    "109": "Claim not covered by this payer/contractor", "110": "Billing date predates service date",
    "112": "Service not furnished directly to patient", "119": "Benefit maximum reached", "129": "Prior processing information appears incorrect",
    "131": "Claim specific negotiated discount", "133": "Service(s) pending review", "140": "Patient/insured ID mismatch",
    "144": "Incentive adjustment (MIPS and similar)", "146": "Diagnosis invalid for date of service",
    "147": "Provider contracted/negotiated rate expired", "150": "Payer deems information does not support this level of service",
    "151": "Payment adjusted: information does not support this many/frequency of services",
    "167": "Diagnosis(es) not covered", "169": "Alternate benefit provided", "170": "Payment denied when performed by this type of provider",
    "171": "Payment denied when performed by this provider in this facility", "172": "Payment adjusted for this type of provider",
    "181": "Procedure code invalid on date of service", "182": "Procedure modifier invalid on date of service",
    "185": "Rendering provider not eligible to perform the service", "186": "Level of care change adjustment",
    "187": "Consumer spending account payments", "197": "Precertification/authorization absent",
    "198": "Precertification/authorization exceeded", "199": "Revenue code and procedure code do not match",
    "200": "Expenses incurred during lapse in coverage", "204": "Service/equipment/drug not covered under plan",
    "208": "NPI not matched", "210": "Payment adjusted: pre-certification/authorization not received in a timely fashion",
    "216": "Based on findings of a review organization", "222": "Exceeds contracted maximum units/hours/days",
    "226": "Information requested from billing provider was not provided/insufficient",
    "227": "Information requested from patient/insured was not provided/insufficient",
    "231": "Mutually exclusive procedures cannot be done same day", "234": "Procedure not paid separately (see RARC)",
    "236": "Procedure or modifier combination not compatible (NCCI or workers comp edits)",
    "242": "Services not provided by network/primary care providers", "243": "Services not authorized by network/primary care providers",
    "246": "Non-payable code for required reporting only", "252": "Attachment/other documentation required to adjudicate",
    "253": "Sequestration: reduction in federal payment", "256": "Service not payable per managed care contract",
    "272": "Coverage/program guidelines not met", "273": "Coverage/program guidelines exceeded",
    "275": "Prior payer's patient responsibility not covered", "276": "Services denied by prior payer not covered",
    "A1": "Claim/service denied (see RARC)", "A8": "Ungroupable DRG", "B5": "Coverage/program guidelines not met",
    "B7": "Provider not certified/eligible to be paid for this procedure on this date", "B8": "Alternative services were available",
    "B10": "Allowed amount reduced because a component of the basic procedure was paid (bundled)",
    "B11": "Claim/service transferred to proper payer", "B12": "Services not documented in patient's medical records",
    "B13": "Previously paid: payment for this claim/service may have been provided in a previous payment",
    "B14": "Only one visit/consultation per physician per day is covered", "B15": "Service requires a qualifying service/procedure to be received and covered",
    "B16": "New patient qualifications were not met", "B20": "Procedure/service partially or fully furnished by another provider",
    "B22": "Payment adjusted based on the diagnosis", "B23": "Procedure billed is not authorized per your CLIA certification",
    "P1": "State-mandated requirement for property and casualty", "W1": "Workers compensation state fee schedule",
    "114": "Procedure/product not approved by the Food and Drug Administration", "B9": "Patient is enrolled in a Hospice",
    "177": "Patient has not met the required eligibility requirements", "288": "Referral absent",
}

# --------------------------------------------------------------------------- RARC
RARC: dict[str, str] = {
    "M15": "Separately billed services/tests have been bundled; they are components of the same procedure",
    "M20": "Missing/incomplete/invalid HCPCS", "M25": "Information furnished does not substantiate the need for this level of service",
    "M26": "Information does not support this many services/supplies", "M27": "Patient was previously notified of liability",
    "M51": "Missing/incomplete/invalid procedure code(s)", "M53": "Missing/incomplete/invalid days or units of service",
    "M54": "Missing/incomplete/invalid total charges", "M62": "Missing/incomplete/invalid treatment authorization code",
    "M64": "Missing/incomplete/invalid other diagnosis", "M76": "Missing/incomplete/invalid diagnosis or condition",
    "M77": "Missing/incomplete/invalid place of service", "M79": "Missing/incomplete/invalid charge",
    "M80": "Not covered when performed during the same session/date as a previously processed service",
    "M81": "Code to the highest level of specificity", "M86": "Service denied: payment already made for same/similar procedure within set time frame",
    "M127": "Missing patient medical record for this service", "M144": "Pre-/post-operative care payment is included in the allowance for the surgery",
    "MA01": "Alert: you may appeal this decision (redetermination within 120 days)", "MA02": "Alert: you may appeal; reconsideration within 180 days",
    "MA04": "Secondary payment cannot be considered without the identity of or payment information from the primary payer",
    "MA07": "Alert: claim information has been forwarded to Medicaid", "MA15": "Alert: claim has been separated to expedite handling",
    "MA18": "Alert: claim information forwarded to the patient's supplemental insurer", "MA61": "Missing/incomplete/invalid Social Security number",
    "MA118": "Alert: no Medicare payment issued for this claim under either Part A or Part B", "MA130": "Claim contains incomplete/invalid information; no appeal rights; resubmit",
    "N4": "Missing/incomplete/invalid prior insurance carrier EOB", "N16": "Family/member out-of-pocket maximum has been met",
    "N19": "Procedure code incidental to primary procedure", "N20": "Service not payable with other service rendered on the same date",
    "N22": "This procedure code was added/changed because it more accurately describes the services rendered",
    "N23": "Alert: patient liability may be affected due to coordination of benefits", "N29": "Missing documentation/orders/notes/summary/report/chart",
    "N30": "Patient ineligible for this service", "N54": "Claim information inconsistent with pre-certified/authorized services",
    "N56": "Procedure code billed is not correct/valid for the services billed or the date of service billed",
    "N59": "Alert: please refer to your provider manual for additional program and provider information",
    "N115": "Decision based on a Local Coverage Determination (LCD)", "N130": "Consult plan benefit documents/guidelines for information about restrictions",
    "N142": "The original claim was denied; resubmit a new claim, not a replacement", "N174": "This is not a covered service/procedure/equipment/bed; patient is liable",
    "N179": "Additional information has been requested from the member", "N185": "Alert: do not resubmit this claim; a replacement claim is being processed",
    "N198": "Rendering provider must be affiliated with the pay-to provider", "N255": "Missing/incomplete/invalid billing provider taxonomy",
    "N381": "Alert: consult contractual agreement for restrictions/billing/payment information", "N390": "This service/report cannot be billed separately",
    "N408": "This payer does not cover deductibles assessed by a previous payer", "N418": "Misrouted claim; see the payer's claim submission instructions",
    "N431": "Not covered with this procedure", "N463": "Missing support data for claim", "N519": "Invalid combination of HCPCS modifiers",
    "N525": "These services are not covered when performed within the global period of another service",
    "N536": "We are not an appropriate payer for this claim", "N570": "Missing/incomplete/invalid credentialing data",
    "N640": "Exceeds number/frequency approved/allowed within time period", "N657": "This should be billed with the appropriate code for these services",
    "N669": "Adjusted based on the Medicare fee schedule", "N702": "Decision based on review of previously adjudicated claims or for claims in process for the same/similar type of services",
    "N706": "Missing documentation", "N770": "The adjustment request received from the provider has been processed; the original claim has been adjusted",
    "N781": "Alert: patient is a Medicaid/QMB beneficiary; do not bill", "N782": "Alert: patient is a Medicaid/QMB beneficiary; review patient liability",
    "N793": "Alert: service must be billed with the appropriate modifier", "N807": "Payment adjustment based on the Merit-based Incentive Payment System (MIPS)",
    "N822": "Missing procedure modifier(s)", "N823": "Incomplete/invalid procedure modifier(s)",
    "MA63": "Missing/incomplete/invalid principal diagnosis", "MA67": "Correction to a prior claim",
    "N104": "This claim/service is not payable under our claim jurisdiction area",
    "N89": "Alert: payment information for this claim has been forwarded to more than one other payer",
}

# --------------------------------------------------------------------------- code attributes
EM_CODES = {"99202", "99203", "99204", "99205", "99211", "99212", "99213", "99214", "99215"}
EM_LEVEL = {c: int(c[-1]) for c in EM_CODES}

# Add-on codes (CMS global indicator ZZZ: never reduced for multiple procedures, paid with their primary) and global
# surgical periods, from CMS's fee schedule relative value file (remitwatch/pfs.py). Codes outside the dermatology ranges
# that file covers and that this practice can bill as add-ons are listed by hand.
from .pfs import PFS_INDICATORS  # noqa: E402

ADD_ON_CODES = {c for c, (glob, _m) in PFS_INDICATORS.items() if glob == "ZZZ"} | {"96367", "96368", "96375", "96376", "G2211"}

# Global surgical period days (Medicare): 0, 10 or 90 for codes with a global period; codes without one are absent.
GLOBAL_DAYS: dict[str, int] = {c: int(glob) for c, (glob, _m) in PFS_INDICATORS.items() if glob in ("000", "010", "090")}

# Codes Medicare reduces as a second or later procedure on the same day (multiple-procedure indicator 2 or 3).
MPPR_CODES = {c for c, (_g, mult) in PFS_INDICATORS.items() if mult in ("2", "3")}

RADIATION_CODES = {"77401", "77402", "77407", "77412", "77427", "77431", "77432", "77435", "77436", "77437", "77280",
                   "77285", "77290", "77295", "77300", "77301", "77306", "77307", "77316", "77317", "77318", "77321",
                   "77331", "77332", "77333", "77334", "77336", "77370", "77385", "77386", "77387", "77600", "77605",
                   "77610", "77615", "77620", "G6001", "G6002", "G6003", "G6004", "G6005", "G6006", "G6007", "G6008",
                   "G6009", "G6010", "G6011", "G6012", "G6013", "G6014", "G6015", "G6016", "G6017", "0394T", "0395T"}

SKIN_SUBSTITUTE_RE = re.compile(r"^Q4[0-9]{3}$|^A2[0-9]{3}$|^C9[0-9]{3}$")
DRUG_RE = re.compile(r"^J[0-9]{4}$")

# Unlisted procedure codes (CPT) and not-otherwise-classified codes (HCPCS): each claim is priced on its own (an
# unclassified drug, J3490, can be any drug), so the amounts a payer allowed for the code are no rate for another claim
# of it (the owner's rule on rate evidence, plans/audit_calibration_2026-11-24.md section 3 fix 5).
UNLISTED_CODES = frozenset({
    "15999", "17999", "19499", "20999", "21299", "21499", "22899", "22999", "23929", "24999", "25999", "26989", "27299",
    "27599", "27899", "28899", "29799", "29999", "30999", "31299", "31599", "36299", "37799", "38999", "40799", "40899",
    "41599", "41899", "42299", "42699", "42999", "46999", "54699", "55899", "58999", "60699", "64999", "66999", "67299",
    "67399", "67599", "67999", "68399", "68899", "69399", "69799", "69949", "69979", "76499", "76999", "77299", "77399",
    "77499", "77799", "79999", "84999", "85999", "86849", "87999", "88099", "88199", "88299", "88399", "89240", "90749",
    "90899", "92499", "93799", "94799", "95199", "95999", "96379", "96549", "96999", "97039", "97139", "97799", "99199",
    "99429", "99499", "99600",
    "J3490", "J3590", "J3591", "J7599", "J7699", "J7799", "J7999", "J8499", "J8597", "J8999", "J9999", "C9399", "A4649",
    "A9698", "A9699", "A9999", "E1399", "L8699", "Q4100", "T1999", "T5999",
})


def is_unlisted(code: str | None) -> bool:
    """An unlisted or not-otherwise-classified code: priced claim by claim, so it has no rate of its own."""
    return (code or "").strip().upper() in UNLISTED_CODES


# The parts of a service a line can bill: the professional reading (modifier 26) and the technical part (TC). A line with
# neither bills the whole service.
COMPONENTS = ("26", "TC")
UNKNOWN_PART = "?"          # a line of a code billed in parts whose part nothing on file names


def component_of(mods) -> str:
    """The part of a service a line bills: '26' (the professional reading), 'TC' (the technical part) or '' (the whole
    service), from its modifiers (a list, JSON text, or text such as '26', '26,59' or '26:59'). The rate tables key on it,
    so the whole service is always '' (never None)."""
    if not mods:
        return ""
    parts = mods if isinstance(mods, (list, tuple)) else re.split(r"[^A-Za-z0-9]+", str(mods))
    for m in parts:
        m = str(m or "").strip().upper()
        if m in COMPONENTS:
            return m
    return ""


def part_fits(want: str, got) -> bool:
    """Whether answers for these parts of a service answer a claim's line for part `want` (the release's T-C1): a 26 by a
    26 or the whole service, a TC by a TC or the whole service, the whole service by the whole service or by both parts.
    A line whose part nothing names (UNKNOWN_PART) answers only a claim for the whole service: for a claim of one part it
    may be the other part."""
    got = set(got or ())
    if want in COMPONENTS:
        return want in got or "" in got
    return "" in got or UNKNOWN_PART in got or set(COMPONENTS) <= got


# What may be shown, exported, searched and printed in a letter as a member number. A payer can also send a Social
# Security number (34), the old Medicare claim number built on one (HN) or a tax ID (FI) in the same place, and a
# nine-digit number labelled as a medical record number can be a Social Security number too; none of those leave the
# server.
MEMBER_QUALS = ("MI", "II", "MR", "")


def member_number(c: dict) -> str:
    """The number to show for a patient: the member number the payer gave for them, else the insured person's (a
    dependent is often listed under the policy holder's). Empty when the payer sent nothing that is a member number."""
    for k in ("patient", "insured"):
        v, qual = (c.get(f"{k}_id") or "").strip(), (c.get(f"{k}_id_qual") or "").strip()
        if v and qual in MEMBER_QUALS and not (qual == "MR" and re.fullmatch(r"\d{9}", v)):
            return v
    return ""


def code_category(code: str | None) -> str:
    if not code:
        return "other"
    if code in EM_CODES:
        return "em"
    if code in RADIATION_CODES or code.startswith("77"):
        return "radiation"
    if SKIN_SUBSTITUTE_RE.match(code):
        return "skin_substitute"
    if DRUG_RE.match(code):
        return "drug"
    if code.startswith("1731") or code in ("17312", "17313", "17314", "17315"):
        return "mohs"
    if code.startswith("1110"):
        return "biopsy"
    if code.startswith("170") or code.startswith("171"):
        return "destruction"
    if code.startswith("114") or code.startswith("116") or code.startswith("113") or code.startswith("112"):
        return "excision"
    if code.startswith("12") or code.startswith("13") or code.startswith("14") or code.startswith("15"):
        return "repair"
    if code.startswith("88"):
        return "pathology"
    if code.startswith("969") or code.startswith("9691"):
        return "phototherapy"
    if code.startswith("119"):
        return "injection"
    if code == "G2211":
        return "g2211"
    if code == "99024":
        return "postop"
    return "other"


# --------------------------------------------------------------------------- payer normalization
# (regex on upper-cased 835 payer name) -> (payer group, kind)
# kind: medicare | medicare_advantage | medicaid | commercial | supplement | tricare | va | other
PAYER_RULES: list[tuple[str, str, str]] = [
    (r"\bKY MEDICARE\b|MEDICARE OF KENTUCKY|^KENTUCKY MEDICARE", "Medicare KY (CGS J15)", "medicare"),
    (r"\bIN MEDICARE\b|MEDICARE OF INDIANA|^INDIANA MEDICARE", "Medicare IN (WPS J8)", "medicare"),
    (r"RAILROAD MEDICARE", "Railroad Medicare", "medicare"),
    (r"^MEDICARE\b", "Medicare (other)", "medicare"),
    # Medicaid in any spelling the practice's systems use: EMA and the 837s name WellCare's Kentucky Medicaid plan with
    # 'Medcaid'. WellCare's Medicaid plan keeps WellCare's group (the one its 835s get from CLP06 MC), so its own windows
    # apply, never the Wellcare Medicare Advantage plan's (deadlines review r2-1)
    (r"WELLCARE.*\bMEDI?CAID\b|\bMEDI?CAID\b.*WELLCARE", "WellCare / Ambetter (Centene) Medicaid", "medicaid"),
    # Anthem's Medicaid plans under the names EMA and the 837s give them ('Anthem Indiana Medicaid Hoosier Healthwise',
    # 'Anthem Pathways Indiana Medicaid'): Anthem's Medicaid group, the one its 835s get from CLP06 MC, so the windows of
    # Anthem's plan in the state the name gives apply, never the state program's (fx-windows)
    (r"^ANTHEM\b.*\bMEDI?CAID\b", "Anthem / BCBS Medicaid", "medicaid"),
    # the state's own fee-for-service program: a name that starts with the state (the 835s' 'KY MEDICAID', 'IN MEDICAID') or
    # with the word Medicaid and then the state (EMA's and the 837s' 'Medicaid of Kentucky', 'Medicaid of Indiana'). A managed
    # care plan's name starts with the plan's own ('<plan> Medicaid of Kentucky', '<plan> Indiana Medicaid'): it is never the
    # state's program, whose rules (Kentucky's no appeal, Indiana's 180 days) are not the plan's (fx-windows)
    (r"^(?:KY|KENTUCKY)\b.*\bMEDI?CAID\b|^MEDI?CAID\b(?:\W+OF)?\W+(?:KY|KENTUCKY)\b", "Medicaid KY", "medicaid"),
    (r"^(?:IN|INDIANA)\b.*\bMEDI?CAID\b|^MEDI?CAID\b(?:\W+OF)?\W+(?:IN|INDIANA)\b", "Medicaid IN", "medicaid"),
    (r"MEDI?CAID", "Medicaid (other)", "medicaid"),
    (r"HUMANA CARE PLAN|HUMANA.*(MEDICARE|ADVANTAGE|GOLD)", "Humana Medicare Advantage", "medicare_advantage"),
    (r"^HUMANA", "Humana", "commercial"),
    (r"AARP.*(ADVANTAGE)|UNITED.*MEDICARE ADV|UHC.*MEDICARE|AARP MEDICARE COMPLETE", "UHC Medicare Advantage", "medicare_advantage"),
    (r"AARP.*SUPPLEMENT|AARP MEDICARE SUPPLEMENT", "AARP Medicare Supplement (UHC)", "supplement"),
    (r"UNITED HEALTHCARE OF ALL STATES|^UHC$|UNITEDHEALTHCARE|UNITED HEALTHCARE$|UNITED HEALTH CARE", "UnitedHealthcare", "commercial"),
    (r"UNITEDHEALTH INTEGRATED|WAUSAU", "UHC Integrated Services (Wausau)", "commercial"),
    (r"UNITED MEDICAL RESOURCES|^UMR\b", "UMR", "commercial"),
    # VA Community Care before Optum: Optum runs part of the VA's network, and its VA remittances say so ("OPTUM VA ...")
    (r"VETERAN|^VA\b|TRIWEST|OPTUM VA|CCN|VACAA", "VA Community Care", "va"),
    (r"OPTUM", "Optum (UHC)", "commercial"),
    (r"SUREST|BIND", "Surest (UHC)", "commercial"),
    (r"ANTHEM.*(MEDICARE|ADVANTAGE)|BLUE.*MEDICARE ADV|BCBS.*MEDICARE", "Anthem Medicare Advantage", "medicare_advantage"),
    (r"^ANTHEM|BLUE CROSS|BLUE SHIELD|^BCBS|BLUECARD|WELLPOINT|ELEVANCE", "Anthem / BCBS", "commercial"),
    (r"AETNA.*(MEDICARE|ADVANTAGE)", "Aetna Medicare Advantage", "medicare_advantage"),
    (r"AETNA.*AMERICAN CONTINENTAL|AMERICAN CONTINENTAL", "Aetna Senior Supplemental (American Continental)", "supplement"),
    (r"^AETNA", "Aetna", "commercial"),
    (r"CIGNA.*(MEDICARE|SUPPLEMENT)", "Cigna Medicare Supplement", "supplement"),
    (r"^CIGNA", "Cigna", "commercial"),
    # Ambetter is Centene's marketplace (individual) plan, never Medicare Advantage or Medicaid whatever CLP06 says
    (r"AMBETTER", "Ambetter (Centene)", "commercial"),
    (r"WELLCARE", "WellCare / Ambetter (Centene)", "medicare_advantage"),
    (r"TRICARE", "Tricare", "tricare"),
    (r"UMWA", "UMWA Health & Retirement Funds", "commercial"),
    (r"MUTUAL OF OMAHA|MEDICO|GREAT AMERICAN LIFE|AMERICAN ENTERPRISE|BANKERS FIDELITY|WELLABE|PHYSICIANS MUTUAL|"
     r"COMBINED INSURANCE|COMPANION LIFE|FORETHOUGHT|ATLANTIC COAST|MANHATTAN LIFE|GLOBAL CARE|RESERVE NATIONAL|"
     r"AMERICAN INS ADMIN|ACTUARIAL MANAGEMENT|STANDARD LIFE|CONTINENTAL LIFE|HEARTLAND NATIONAL|GOLDEN RULE|"
     r"LUMICO|SHENANDOAH|AMERICAN RETIREMENT|TRANSAMERICA|STATE FARM|USAA|NEW ERA|EVEREST|CENTRAL STATES|ELIPS|"
     r"OMAHA|SUPPLEMENT|MEDIGAP|UNITED AMERICAN|COLONIAL PENN|GERBER|LIBERTY NATIONAL|GLOBE LIFE|THRIVENT|"
     r"OXFORD LIFE|ACCENDO|AMERICAN FAMILY|FIRST HEALTH LIFE", "Medicare Supplement carriers", "supplement"),
    (r"AUXIANT|DUNN AND ASSOCIATES|MERITAIN|HEALTHCOMP|ALLIANCE COAL|BENEFIT ADMINISTRATIVE|ADMINISTRATIVE CONCEPTS|"
     r"LIFEPRINT|TPA|BENEFIT|ADMINISTRATOR|TRUSTMARK|ALLIED|GROUP & PENSION|HEALTHSCOPE|UMR", "Self-funded plan TPAs", "commercial"),
]


def normalize_payer(name: str | None) -> tuple[str, str]:
    """Return (payer_group, kind) for an 835 payer name."""
    if not name:
        return ("Unknown", "other")
    u = name.upper()
    for pattern, group, kind in PAYER_RULES:
        if re.search(pattern, u):
            return (group, kind)
    return (name.strip().title(), "other")


def payer_kind_from_cfi(cfi: str | None, default: str) -> str:
    """Refine kind with the claim filing indicator (CLP06)."""
    if cfi == "16":
        return "medicare_advantage"
    if cfi in ("MA", "MB"):
        return "medicare"
    if cfi == "MC":
        return "medicaid"
    if cfi in ("CH", "TV"):
        return "tricare"
    if cfi == "VA":
        return "va"
    return default


# --------------------------------------------------------------------------- place of service
# CLP08 place-of-service codes that Medicare (and payers that follow it) pay at the facility rate.
FACILITY_POS = {"02", "19", "21", "22", "23", "24", "26", "31", "34", "41", "42", "51", "52", "53", "56", "61"}


def pos_class(pos: str | None) -> str:
    """'facility' when the claim's place of service is paid at the facility rate, else 'office'."""
    return "facility" if (pos or "") in FACILITY_POS else "office"


# --------------------------------------------------------------------------- which plan an item is
# One payer group can hold plans with different windows: Anthem's Medicaid plans in Kentucky and in Indiana, the Federal
# Employee Program inside Anthem's commercial group, Oxford and the other payers the name rules do not know whose claims
# carry Medicare's filing indicator. What tells them apart is what the remittance or the 837 carries: the plan (the 835's
# contract class, REF*CE), the payer's name and id, the claim filing indicator (CLP06), the state a plan's or a payer's
# name gives, the denial's reason (CARC) and whether the line was paid. appeal_rule, filing_rule and corrected_rule take
# them as optional keyword arguments (rule_facts reads them off an item, a line or a claim), so every call site keeps
# working without them. A rule that needs a fact the item does not carry is passed over: an item that does not say which
# plan it is gets the shorter window, and the label of that window says so.
PLAN_FACTS = ("plan", "payer_name", "payer_id", "cfi", "state", "carc", "paid")

# The VA's community care networks: claims go out under these payer ids, and EMA and the 837s name the network after its
# administrator ('Optum Unitedhealth Care'), whose name the rules read as UnitedHealthcare's administered plans. Where a
# claim carries one of these ids, the VA's windows apply whatever group its name gave (A17 review a17-r1-3, fx-windows).
# detect.VA_PAYER_IDS and ema_reports.VA_PAYER_IDS hold the same ids (tests/test_fx_windows.py checks they agree).
VA_PAYER_IDS = frozenset({"VACCN", "TWVACCN"})
VA_GROUP = ("VA Community Care", "va")


def _by_payer_id(grp: str, kind: str, f: dict) -> tuple[str, str]:
    """The group and kind a window is looked up by: the VA's network where the claim carries its payer id, else the ones
    given."""
    if str(f.get("payer_id") or "").strip().upper() in VA_PAYER_IDS:
        return VA_GROUP
    return grp, kind


US_STATES = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR", "CALIFORNIA": "CA", "COLORADO": "CO", "CONNECTICUT": "CT",
    "DELAWARE": "DE", "DISTRICT OF COLUMBIA": "DC", "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI", "IDAHO": "ID", "ILLINOIS": "IL",
    "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS", "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME", "MARYLAND": "MD",
    "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN", "MISSISSIPPI": "MS", "MISSOURI": "MO", "MONTANA": "MT",
    "NEBRASKA": "NE", "NEVADA": "NV", "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ", "NEW MEXICO": "NM", "NEW YORK": "NY",
    "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK", "OREGON": "OR", "PENNSYLVANIA": "PA",
    "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC", "SOUTH DAKOTA": "SD", "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT",
    "VERMONT": "VT", "VIRGINIA": "VA", "WASHINGTON": "WA", "WEST VIRGINIA": "WV", "WISCONSIN": "WI", "WYOMING": "WY"}
# the longest names first, so WEST VIRGINIA is not read as VIRGINIA
_STATE_RE = re.compile(r"\b(" + "|".join(sorted(US_STATES, key=len, reverse=True)) + r")\b")


def plan_state(plan: str | None = None, payer_name: str | None = None) -> str | None:
    """The state (its two-letter code) the plan's name (the 835's contract class, which for Anthem's Medicaid plans starts
    with KENTUCKY, INDIANA or IN) or else the payer's name (a plan 'OF KENTUCKY') names; None when neither names one. Only
    KY and IN have rules of their own: a plan of another state is told apart from one that names no state, so its item can
    say which it is (deadlines review r1-9)."""
    for text in (plan, payer_name):
        u = " ".join(str(text or "").upper().split())
        if not u:
            continue
        # the state's code after 'of' ('<plan> Medicaid of Ky', as EMA names a plan) names it too (fx-windows)
        if re.search(r"\bKENTUCKY\b|\bOF KY\b", u) or u.startswith("KY "):
            return "KY"
        if re.search(r"\bINDIANA\b|\bOF IN\b", u) or u.startswith("IN "):
            return "IN"
        m = _STATE_RE.search(u)
        if m:
            return US_STATES[m.group(1)]
    return None


def rule_facts(item=None, **over) -> dict:
    """The plan facts of an item (a finding), an 835 line or a claim, as appeal_rule's, filing_rule's and corrected_rule's
    keyword arguments: its plan (the contract class), the payer's name and id, the claim filing indicator, the denial's
    reason (CARC) and whether the line was paid (None when not known). `over` sets or replaces any of them."""
    get = item.get if item is not None and hasattr(item, "get") else (lambda _k, d=None: d)
    plan = get("plan") or get("contract_class") or ""
    plan = plan.strip() if isinstance(plan, str) else ""
    paid = get("paid_cents")
    facts = {"plan": plan or None, "payer_name": get("payer_name"), "payer_id": get("payer_id"),
             "cfi": get("claim_filing_indicator"), "carc": get("carc"),
             "paid": None if paid is None else int(paid or 0) > 0}
    facts.update({k: v for k, v in over.items() if k in PLAN_FACTS})
    return facts


def _facts(kw: dict) -> dict:
    """A call's plan facts, with the state the plan's or the payer's name gives where none is passed. An unknown keyword
    is refused, so a misspelt fact fails at once instead of quietly giving the shorter window."""
    bad = sorted(set(kw) - set(PLAN_FACTS))
    if bad:
        raise TypeError(f"unknown plan facts: {', '.join(bad)}")
    f = dict(kw)
    if not f.get("state"):
        f["state"] = plan_state(f.get("plan"), f.get("payer_name"))
    return f


def _fits(r: dict, grp: str, kind: str, f: dict) -> bool:
    """Whether a window rule applies to this payer group and kind with these facts. Each of a rule's conditions ('plan': a
    pattern on the plan's name, 'state', 'other_state': a state the item names that is none of these, 'carcs': the
    denial's reason is one of these, 'other_carcs': it is known and none of these, 'paid', 'cfi') needs its fact: a fact the
    item does not carry fails the condition."""
    if not re.search(r["match"], grp) or (r.get("kinds") is not None and kind not in r["kinds"]):
        return False
    if "plan" in r and not re.search(r["plan"], str(f.get("plan") or "").upper()):
        return False
    if "state" in r and f.get("state") != r["state"]:
        return False
    if "other_state" in r and (not f.get("state") or f.get("state") in r["other_state"]):
        return False
    carc = str(f.get("carc") or "")
    if "carcs" in r and carc not in r["carcs"]:
        return False
    if "other_carcs" in r and (not carc or carc in r["other_carcs"]):
        return False
    if "paid" in r and f.get("paid") is not r["paid"]:
        return False
    if "cfi" in r and str(f.get("cfi") or "") not in r["cfi"]:
        return False
    return True


def _public(r: dict, **extra) -> dict:
    """The rule as the call sites read it: days (the length in days, for a caller that counts days; a window in months or
    to a year's end is longer or equal), label, source, verified, months, year_end, receipt_days, legal, since and before
    (a window that applies from an effective date, and the one before it), no_appeal (and no_appeal_step, the words of the
    work a no-appeal payer takes instead), cob (how the payer counts a claim it gets as the second payer:
    second_claim_window), and the clock, its date-of-service alternative (or_dos), resubmit_denials or the earliest date
    where the kind of window has them."""
    out = {k: r[k] for k in ("days", "label", "source", "verified")}
    out.update(months=r.get("months"), year_end=r.get("year_end"), receipt_days=int(r.get("receipt_days") or 0), legal=r.get("legal"),
               since=r.get("since"), before=_public(r["before"]) if r.get("before") else None, no_appeal=bool(r.get("no_appeal")),
               no_appeal_step=r.get("no_appeal_step"), cob=r.get("cob"))
    for k in ("clock", "or_dos", "resubmit_denials", "crossover"):
        if k in r:
            out[k] = r[k]
    out.update(extra)
    return out


# --------------------------------------------------------------------------- appeal windows
# Days from the remittance (EOP) date to file the first-level dispute, per the payer's published provider manual, the
# regulation or the statute. `verified` is True only where the wording was read in the primary source (docs/RESEARCH_SUMMARY.md
# lists the sources and their words). A participation agreement can set a different period, so every deadline shown by
# the dashboard is an estimate. A window the payer states in months or years ("months") is counted in calendar months to
# the same day of the month (window_end); one that runs to the end of a later year ("year_end") ends on its December 31;
# "days" is then the shortest length the window can have, for a caller that counts days.
#
# The practice bills the large Medicare Advantage plans (Humana, UnitedHealthcare, Anthem) and Ambetter as a contracted
# (participating) provider: their volume and their contracted payments say so. A provider that is not contracted has a
# shorter window (60 days from receipt of the notice, 42 CFR 422.582(b)); the labels name it, the dates do not use it.
#
# KRS 205.534(1)(d)1, as amended by 2026 Ky. Acts ch. 179 (effective April 14, 2026): every Kentucky Medicaid managed care
# plan must let a provider "file any grievance or appeal related to the reduction or denial of the claim within one hundred
# twenty (120) days of confirmed receipt of a notification from the managed care organization that payment for a submitted
# claim has been reduced or denied". The statute counts from the notice of the denial, so the window applies by the date of
# the denial (the remittance): a remittance dated on or after April 14, 2026 has it, an earlier one the plan's own window.
# Receipt is presumed 5 days after the remittance, as the product presumes for Medicare (42 CFR 405.942(a)(1)), so the
# plan's last day is 125 days after the remittance; the plan's own shorter window stays the safe date.
KRS_205_534_SINCE = "2026-04-14"
KY_MCO_LEGAL = {"days": 125}
KY_MCO_WORDS = ("since April 14, 2026 Kentucky law lets a provider appeal a reduced or denied claim 'within one hundred twenty (120) days "
                "of confirmed receipt' of the notice (KRS 205.534(1)(d)1), counted here from the remittance plus 5 days for receipt")
KRS_SOURCE = "KRS 205.534(1)(d)1 (2026 Ky. Acts ch. 179, effective April 14, 2026)"
NONCONTRACTED_MA = ("A provider not contracted with the plan asks for a reconsideration within 60 calendar days of receipt of the "
                    "organization determination, presumed 5 days after its date (42 CFR 422.582(b)), with a waiver of liability")
# WellCare of Kentucky's Medicaid plan has two appeal clocks (its 2026 Medicaid Quick Reference Guide and 2025-2026 manual).
# CLAIM PAYMENT APPEALS, 24 months from the EOP: 'claim denials for issues related to untimely filing, incidental
# procedures, unlisted procedure codes and non-covered codes'. CLINICAL APPEALS, 60 calendar days: a denial 'for lack of
# prior authorization, services exceeding the authorization, insufficient documentation or late notification ... Anything
# else related to authorization, or medical necessity'. The reasons below are the claim-payment ones (untimely filing 29;
# incidental and bundled 97, B15, 234, 236; non-covered 96, 204, 167); every other or unknown reason gets the clinical
# window, the shorter, with the statute's 120 days from receipt as its last day (deadlines review r1-3)
WELLCARE_KY_PAYMENT_CARCS = frozenset({"29", "97", "B15", "234", "236", "96", "204", "167"})
# the clinical ones the guide names, for the label and the tests: authorization 15, 39, 62, 197, 198, 243, 288; medical
# necessity 50, 55, 56, 150, 151, 152; documentation 226, 251, 252, B12; late notification 210
WELLCARE_KY_CLINICAL_CARCS = frozenset({"15", "39", "62", "197", "198", "243", "288", "50", "55", "56", "150", "151", "152",
                                        "226", "251", "252", "B12", "210"})
# a clinical decision: not medically necessary, experimental or investigational. Anthem's Claim Payment Dispute 'does not
# apply to appeals regarding a clinical decision denial' (its Kentucky provider manual, Claim Payment Disputes)
MEDICAL_NECESSITY_CARCS = frozenset({"50", "55", "56", "150", "151", "152"})
# Anthem's commercial clinical appeal: a claim 'denied as not medically necessary or experimental/investigational' (its 2026
# manuals, Clinical Appeals): CARC 50, 55 and 56. The level-of-service, frequency and length denials (150, 151, 152, 'the
# information submitted does not support') stay with the Claim Payment Dispute: Anthem reviews an E&M level 'in accordance
# with correct coding requirements and/or reimbursement policy', and the dispute covers 'Claim code editing issues' (fx-windows)
ANTHEM_CLINICAL_CARCS = frozenset({"50", "55", "56"})
# A BlueCard member's plan, by the 835's plan name (its contract class): BlueCard's own words ('ITS HOST', 'BLUECARD',
# 'BLUE CARD'), or a name that starts with the two-letter code of a state other than Kentucky and Indiana ('GA ... PPO').
# A plan that spells out another state's name is found by its state (the rows' other_state).
ANTHEM_HOME_PLAN = (r"\bITS\b|\bHOST\b|\bBLUE ?CARD\b|^\s*(?:"
                    + "|".join(sorted(set(US_STATES.values()) - {"KY", "IN"})) + r")\b")
ANTHEM_CLINICAL_SOURCE = ("Anthem Blue Cross and Blue Shield Kentucky Commercial Provider Manual (2026, v1 effective September 1, 2026 and "
                          "v2 effective March 1, 2027) and Indiana Provider Manual (2026, v1 effective August 1, 2026), Clinical Appeals, "
                          "Claim Payment Disputes and BlueCard Members")
ANTHEM_BLUECARD_CLINICAL = (
    "Anthem commercial, a BlueCard member's clinical denial (not medically necessary or experimental/investigational; the plan is "
    "another Blue plan's): such appeals 'are the responsibility of the Blue Plan ... (the Member's Home Plan)', and 'if the appeal "
    "relates to a Claim denial, and the Provider or Facility did not receive this information from the Member's Home Plan', 'a "
    "Claim Payment Dispute should be submitted ... to the local Anthem Plan', two years from the EOP: the last day. The clinical "
    "appeal's 180 days from the remittance are the date shown, since the Home Plan's own directions can be that short")
GEHA_MATCH = r"(?i)^(geha\b|government employees (health|hospital) association)"
FEP_PLAN = r"FEDERAL EMPLOYEE"
# the Federal Employee Program reaches the practice through Anthem (the 835's contract class names it); Anthem's provider
# manual, not the member's brochure, sets a participating provider's limits for it (deadlines reviews r1-1, r2-3, r2-4).
# Its rows reach Anthem's group and a payer named for the program itself (normalize_payer keeps an unknown name as its
# group: 'Federal Employee Program', 'Fep Blue'); another payer's remittance keeps its own payer's windows whatever plan
# words it carries (Medicare's, TRICARE's, a Medicaid plan's)
FEP_GROUP = r"^(?:Anthem / BCBS$|Federal Employee Program\b|Fep\b)"
FEHBP_MANUAL = ("Anthem Blue Cross and Blue Shield Kentucky Provider Manual (2026, v1 and v2) and Indiana Provider Manual (2026), "
                "Submission of Claims under the FEHBP and PSHBP")
KY_FFS_NO_APPEAL_STEP = ("send a corrected or new claim within 12 months of the date of service (907 KAR 1:673); Kentucky Medicaid "
                         "takes no appeal of an ordinary claim denial (907 KAR 1:671)")
# a payer the name rules do not know (its group is its own name) whose claims carry Medicare's filing indicator (MA, MB):
# payer_group_kind calls it Medicare, but it is not Medicare, and Medicare's windows are not its own
NOT_MEDICARE_ITSELF = r"^(?!Medicare\b|Railroad Medicare\b)"

APPEAL_RULES: list[dict] = [
    # The Federal Employee Program, which the practice bills through Anthem as a participating provider. Anthem's provider
    # manual sets the provider's dispute: its Claim Payment Dispute ('Contractual payment issues', 'Disagreements over reduced
    # or zero-paid Claims', 'Claim code editing issues', 'Timely filing issues') has 'a minimum of two (2) years from the issue
    # date of the EOP', with no exception for the FEP; it 'does not apply to appeals regarding a clinical decision denial'
    # (not medically necessary, experimental), which go through the FEP's disputed claims process: a reconsideration 'within
    # 6 months from the date of the notice' (5 CFR 890.105(b)(1)), the member's, or the provider's for the member with written
    # consent. 180 days stays the safe date (deadlines review r2-4).
    {"match": FEP_GROUP, "plan": FEP_PLAN, "kinds": None, "carcs": MEDICAL_NECESSITY_CARCS, "days": 180, "verified": False, "legal": {"months": 6},
     "label": "Federal Employee Program, a clinical denial (not medically necessary or experimental): 180 days used as a safe date; Anthem's "
              "Claim Payment Dispute 'does not apply to appeals regarding a clinical decision denial', and the FEP's disputed claims process "
              "takes a reconsideration 'within 6 months from the date of the notice' (5 CFR 890.105(b)(1)), filed for the member with "
              "the member's written consent",
     "source": "Anthem Blue Cross and Blue Shield Kentucky Provider Manual (2026 v2), Claim Payment Disputes and Clinical Appeals; "
               "5 CFR 890.105(b)(1) (eCFR as of 2026-11-24); 2026 Service Benefit Plan brochure RI 71-005, Section 8"},
    {"match": FEP_GROUP, "plan": FEP_PLAN, "kinds": None, "days": 180, "verified": False, "legal": {"years": 2},
     "label": "Federal Employee Program: 180 days used as a safe date. A provider's Claim Payment Dispute (a contractual payment issue, a "
              "reduced or zero-paid claim, a coding edit, timely filing) has 'a minimum of two (2) years from the issue date of the EOP' "
              "in Anthem's provider manual, which makes no exception for the FEP: that is the last day used. The member's own "
              "reconsideration is due within 6 months of the denial notice (5 CFR 890.105(b)(1)); the FEP's provider dispute period is "
              "not confirmed",
     "source": "Anthem Blue Cross and Blue Shield Kentucky Provider Manual (2026 v2), Claim Payment Disputes; 5 CFR 890.105(b)(1) "
               "(eCFR as of 2026-11-24)"},
    # A clinical decision (not medically necessary, experimental or investigational) of Anthem's own member is appealed as a
    # clinical appeal, not by the Claim Payment Dispute below, which 'does not apply to appeals regarding a clinical decision
    # denial'. The manuals count the 180 days from receipt of the notice: the remittance's date is the safe date, and receipt
    # presumed 5 days after it (as for KRS 205.534 above) the last day. A BlueCard member (another Blue plan's, the Home
    # Plan's) is not Anthem's: the Home Plan decides a clinical appeal, and a claim denial the provider appeals without the
    # Home Plan's directions goes to the local plan as a Claim Payment Dispute (the manuals, BlueCard Members), two years from
    # the EOP. The 835's plan tells them apart (ANTHEM_HOME_PLAN, or a plan or payer of a state other than Kentucky and
    # Indiana); their denial and one whose plan is not known (EMA's) keep the clinical appeal's 180 days as the date shown
    # (the Home Plan's directions can be that short) and the dispute's two years as the last day: a guess never makes an
    # item expire early (fx-windows)
    {"match": r"^Anthem / BCBS$", "kinds": {"commercial", "supplement", "other"}, "carcs": ANTHEM_CLINICAL_CARCS, "plan": ANTHEM_HOME_PLAN,
     "days": 180, "verified": True, "legal": {"years": 2}, "label": ANTHEM_BLUECARD_CLINICAL, "source": ANTHEM_CLINICAL_SOURCE},
    {"match": r"^Anthem / BCBS$", "kinds": {"commercial", "supplement", "other"}, "carcs": ANTHEM_CLINICAL_CARCS, "other_state": ("KY", "IN"),
     "days": 180, "verified": True, "legal": {"years": 2}, "label": ANTHEM_BLUECARD_CLINICAL, "source": ANTHEM_CLINICAL_SOURCE},
    {"match": r"^Anthem / BCBS$", "kinds": {"commercial", "supplement", "other"}, "carcs": ANTHEM_CLINICAL_CARCS, "plan": r"\S",
     "days": 180, "verified": True, "legal": {"days": 185},
     "label": "Anthem commercial, a clinical appeal (a claim 'denied as not medically necessary or experimental/investigational'): "
              "'Providers and Facilities have one hundred eighty (180) calendar days to file a clinical appeal from the date they receive "
              "notice of Anthem's initial decision'. The Claim Payment Dispute's two years 'does not apply to appeals regarding a clinical "
              "decision denial'. Counted here from the remittance; the last day presumes the notice was received 5 days after it",
     "source": ANTHEM_CLINICAL_SOURCE},
    {"match": r"^Anthem / BCBS$", "kinds": {"commercial", "supplement", "other"}, "carcs": ANTHEM_CLINICAL_CARCS,
     "days": 180, "verified": True, "legal": {"years": 2},
     "label": "Anthem commercial, a clinical denial (not medically necessary or experimental/investigational) whose plan is not known: "
              "Anthem's own member has 180 calendar days from the notice to file a clinical appeal, the date shown here, counted from the "
              "remittance; a BlueCard member's (another Blue plan's) denial goes to the local Anthem plan as a Claim Payment Dispute, "
              "two years from the EOP, the last day used until the plan is known",
     "source": ANTHEM_CLINICAL_SOURCE},
    {"match": r"^Anthem / BCBS$", "kinds": {"commercial", "supplement", "other"}, "days": 730, "months": 24, "verified": True, "legal": {"years": 2},
     "label": "Anthem commercial: Claim Payment Reconsideration within two years of the EOP issue date; "
              "Claim Payment Appeal within 90 days of the reconsideration decision",
     "source": "Anthem Blue Cross and Blue Shield Kentucky Commercial Provider Manual, effective September 1, 2026, Claim Payment Disputes"},
    {"match": r"^Anthem", "kinds": {"medicare_advantage"}, "days": 120, "verified": True,
     "label": "Anthem Medicare Advantage: reconsideration within 120 days of the date on the EOP; appeal within 30 days of the "
              "reconsideration letter. A provider not contracted with the plan asks for a reconsideration of a denial 'within 65 calendar "
              "days from the remittance notification' instead, with a waiver of liability (guidebook 7.2)",
     "source": "Anthem Blue Cross and Blue Shield Medicare Advantage Provider Guidebook, edition of January 1, 2027, 5.3 and 7.2"},
    {"match": r"^Anthem", "kinds": {"medicaid"}, "state": "KY", "days": 90, "verified": True, "since": KRS_205_534_SINCE, "legal": KY_MCO_LEGAL,
     "label": "Anthem Kentucky Medicaid: payment appeal within 90 calendar days of the remittance date (its Kentucky Medicaid manual); "
              + KY_MCO_WORDS,
     "source": "Anthem Blue Cross and Blue Shield Kentucky Medicaid Provider Manual, payment appeals; " + KRS_SOURCE,
     "before": {"days": 90, "verified": True, "source": "Anthem Blue Cross and Blue Shield Kentucky Medicaid Provider Manual, payment appeals",
                "label": "Anthem Kentucky Medicaid: payment appeal within 90 calendar days of the remittance date (a denial before "
                         "April 14, 2026, when KRS 205.534's 120 days began)"}},
    {"match": r"^Anthem", "kinds": {"medicaid"}, "state": "IN", "days": 60, "verified": True,
     "label": "Anthem Indiana Medicaid: a claim dispute 'must be received by Anthem within 60 calendar days from the date on the EOB'; an "
              "appeal of its answer within 60 calendar days of that answer",
     "source": "Anthem Blue Cross and Blue Shield (Indiana Medicaid), IHCP Works 2026: Top denials and quick tips, October 2026"},
    {"match": r"^Anthem", "kinds": {"medicaid"}, "other_state": ("KY", "IN"), "days": 60, "verified": False,
     "label": "Anthem Medicaid: the remittance names a plan of another state than Kentucky or Indiana; that plan's windows are not "
              "checked, so Indiana's shorter one is used: a claim dispute within 60 calendar days from the date on the EOB",
     "source": "Anthem (Indiana Medicaid), IHCP Works 2026: Top denials and quick tips (the other state's plan not checked)"},
    {"match": r"^Anthem", "kinds": {"medicaid"}, "days": 60, "verified": True,
     "label": "Anthem Medicaid: the remittance does not say whether this is Anthem's Kentucky or its Indiana Medicaid plan, so the "
              "shorter window is used: Indiana's claim dispute within 60 calendar days from the date on the EOB (Kentucky: a payment "
              "appeal within 90 days, and at least 120 days from receipt since April 14, 2026)",
     "source": "Anthem (Indiana Medicaid), IHCP Works 2026: Top denials and quick tips; Anthem Kentucky Medicaid Provider Manual; " + KRS_SOURCE},
    {"match": r"^(UnitedHealthcare|UHC)(?! Integrated)", "kinds": None, "days": 365, "months": 12, "verified": True, "legal": {"months": 12},
     "label": "UnitedHealthcare: reconsideration and appeal together within 12 months of the original EOB or PRA (one window for both steps). "
              "On a Medicare Advantage plan, a provider not contracted with it has 60 days from receipt of the notice instead (42 CFR 422.582(b))",
     "source": "UnitedHealthcare Administrative Guide 2026, chapter 10 (revised May 13, 2026); uhcprovider.com appeals page"},
    {"match": r"^(UMR|Optum|Surest|UHC Integrated)", "kinds": None, "days": 365, "months": 12, "verified": False, "legal": {"months": 12},
     "label": "UnitedHealthcare-administered plan: 12 months from the original EOB or PRA, as for UnitedHealthcare",
     "source": "UnitedHealthcare Administrative Guide 2026 (the plan's own rules not checked)"},
    # an Indiana Medicaid managed care plan no row of its own covers (Anthem's has its own above): the plan's dispute window
    # is not checked; 60 days, the shortest known (Anthem's Indiana plan, the program's own administrative review), is used,
    # which is also the window an Indiana plan's name was given while it was read as the program itself (fx-windows)
    {"match": r"^(?!Medicaid (?:IN|KY)$)", "kinds": {"medicaid"}, "state": "IN", "days": 60, "verified": False,
     "label": "Indiana Medicaid managed care plan: the plan's own dispute window is not checked; 60 days from the remittance is used, the "
              "window Anthem's Indiana plan gives ('within 60 calendar days from the date on the EOB') and the program gives for its own "
              "administrative review",
     "source": "Anthem (Indiana Medicaid), IHCP Works 2026: Top denials and quick tips; IHCP Provider Reference Module, Claim "
               "Administrative Review and Appeals (the plan's own manual not checked)"},
    {"match": r"^Humana", "kinds": {"medicaid"}, "days": 120, "verified": False,
     "label": "Humana Medicaid: 120 days used. Humana Healthy Horizons in Kentucky takes an appeal of a denial or reduction within 120 days "
              "(KRS 205.534) and a contracted-rate dispute within 24 months; the Indiana plan allows 60 days. The remittance does not say which plan",
     "source": "Humana Healthy Horizons in Kentucky appeals page (2026) and KRS 205.534; Humana Healthy Horizons in Indiana provider manual (2026)"},
    {"match": r"^Humana", "kinds": {"medicare_advantage"}, "days": 540, "months": 18, "verified": True, "legal": {"months": 18},
     "label": "Humana: claim dispute within 18 months of receiving the original claim determination (counted here from the remittance date). "
              + NONCONTRACTED_MA,
     "source": "Humana Provider Manual 2026 (773902ALL0725), claims disputes, for all products; 42 CFR 422.582(b) (eCFR as of 2026-11-24)"},
    {"match": r"^Humana", "kinds": None, "days": 540, "months": 18, "verified": True, "legal": {"months": 18},
     "label": "Humana: claim dispute within 18 months of receiving the original claim determination (counted here from the remittance date)",
     "source": "Humana Provider Manual 2026 (773902ALL0725), claims disputes, for all products"},
    {"match": r"^WellCare", "kinds": {"medicaid"}, "state": "KY", "carcs": WELLCARE_KY_PAYMENT_CARCS, "days": 730, "months": 24,
     "verified": True, "legal": {"months": 24},
     "label": "WellCare of Kentucky Medicaid: a claim payment appeal ('claim denials for issues related to untimely filing, incidental "
              "procedures, unlisted procedure codes and non-covered codes') 'within 24 months of the date on the EOP'",
     "source": "WellCare of Kentucky 2026 Medicaid Quick Reference Guide, Claim Payment Appeals; 2025-2026 Kentucky Medicaid Provider "
               "Manual, Claim Payment Appeals"},
    {"match": r"^WellCare", "kinds": {"medicaid"}, "state": "KY", "days": 60, "verified": True, "since": KRS_205_534_SINCE, "legal": KY_MCO_LEGAL,
     "label": "WellCare of Kentucky Medicaid: a clinical appeal 'within 60 calendar days of a claims denial for lack of prior authorization, "
              "services exceeding the authorization, insufficient documentation or late notification', or anything related to "
              "authorization or medical necessity (its 2026 Quick Reference Guide); " + KY_MCO_WORDS + ". A claim payment appeal of an "
              "untimely filing, bundling or non-covered code denial has 24 months; where the denial's reason is none of those or not known, "
              "the shorter window is used",
     "source": "WellCare of Kentucky 2026 Medicaid Quick Reference Guide, Clinical Appeals; 2025-2026 Kentucky Medicaid Provider Manual, "
               "Provider Appeals Process; " + KRS_SOURCE,
     "before": {"days": 60, "verified": True, "source": "WellCare of Kentucky 2025-2026 Kentucky Medicaid Provider Manual, Provider Appeals Process",
                "label": "WellCare of Kentucky Medicaid: an appeal within 60 calendar days of the denial (a denial before April 14, 2026, "
                         "when KRS 205.534's 120 days began)"}},
    {"match": r"^(?!Medicaid KY$)", "kinds": {"medicaid"}, "state": "KY", "days": 120, "verified": True, "since": KRS_205_534_SINCE,
     "legal": KY_MCO_LEGAL,
     "label": "Kentucky Medicaid managed care plan: the plan's own window is not checked; " + KY_MCO_WORDS + ". The date shown is "
              "120 days from the remittance",
     "source": KRS_SOURCE,
     "before": {"days": 90, "verified": False, "source": "conservative default for a Medicaid managed care plan",
                "label": "Kentucky Medicaid managed care plan: 90 days is the conservative default for a denial before April 14, 2026 "
                         "(the plan's own window is not checked)"}},
    {"match": r"^Medicaid IN$", "kinds": {"medicaid"}, "days": 60, "verified": True,
     "label": "Indiana Medicaid (fee-for-service): 'Requests for administrative review must be filed within 60 calendar days of notification "
              "of claim payment or denial' (the date on the RA); an appeal of the review's decision within 15 calendar days of it",
     "source": "IHCP Provider Reference Module, Claim Administrative Review and Appeals (published March 24, 2026), Introduction"},
    {"match": r"^Medicaid KY$", "kinds": {"medicaid"}, "days": 30, "verified": True, "legal": {"days": 35}, "no_appeal": True,
     "no_appeal_step": KY_FFS_NO_APPEAL_STEP,
     "label": "Kentucky Medicaid (fee-for-service) takes no appeal of an ordinary claim denial: its dispute resolution, 'within thirty (30) "
              "calendar days of the date the notice was received', is only for the department's own determinations (an overpayment to "
              "repay, a sanction, payments withheld: 907 KAR 1:671 Sections 8 and 9). A denied claim is fixed by a corrected or new "
              "claim within 12 months of the date of service (907 KAR 1:673). A dispute is counted here from the remittance plus 5 days "
              "for receipt",
     "source": "907 KAR 1:671 Sections 8(1) and 9(4) (certified effective September 24, 2026); 907 KAR 1:673 Section 2(1)(b)"},
    # Aetna's dispute page covers its commercial and Medicare plans only; its Medicaid plans (Aetna Better Health) and the
    # supplement group are not on it (deadlines review r1-6)
    {"match": r"^Aetna", "kinds": {"commercial", "medicare_advantage"}, "days": 180, "verified": True,
     "label": "Aetna: 'File your reconsideration within 180 calendar days of the initial claim decision'; an appeal within 60 days of the "
              "reconsideration decision. For a fully insured plan Aetna allows 2 years in Kentucky (participating providers) and Indiana; "
              "the remittance does not say whether the plan is fully insured, so the shorter window is used",
     "source": "Aetna, Health care professionals: Disputes and appeals overview; Dispute & appeal process: state exceptions to filing "
               "standard (read 2026-11-26)"},
    {"match": r"^Aetna", "kinds": {"medicaid"}, "days": 90, "verified": False, "legal": {"days": 180},
     "label": "Aetna Medicaid (Aetna Better Health): the remittance does not say the plan's state and the plan's own window is not checked; "
              "90 days, the Medicaid default, is the safe date, and Aetna's 180 days for its other plans the last day used",
     "source": "conservative default for a Medicaid managed care plan; Aetna, Disputes and appeals overview (commercial and Medicare plans)"},
    {"match": r"^Cigna", "kinds": None, "days": 180, "verified": False,
     "label": "Cigna: payment review within 180 days of the initial payment or denial",
     "source": "Cigna reference guide (as reported in research)"},
    {"match": r"(?i)^champ\s*-?\s*va", "kinds": None, "days": 365, "months": 12, "verified": True, "legal": {"months": 12},
     "label": "CHAMPVA: a request for reconsideration 'within one year of the date of the initial determination' (38 CFR 17.277(b); one "
              "year also under 38 CFR 3.2500(a)(1)); a review by VA within 90 days of that decision",
     "source": "38 CFR 17.277(b) and 3.2500(a)(1) (eCFR as of 2026-11-24)"},
    # GEHA's brochure speaks to the member; no GEHA provider source was read, so its 6 months is the member's own window, used
    # as the last day (a provider files it for the member with written consent), and not confirmed for a network provider
    {"match": GEHA_MATCH, "kinds": None, "days": 180, "verified": False, "legal": {"months": 6},
     "label": "GEHA (Federal Employees Health Benefits): 'Write to us within 6 months from the date of our decision' (5 CFR 890.105(b)(1)), "
              "the member's own request, which a provider files for the member with written consent; the date shown is 180 days, the last "
              "day 6 months; then OPM's review within 90 days. A network provider's own dispute period is not confirmed",
     "source": "2026 GEHA Benefit Plan brochure RI 71-006, Section 8, step 1; 5 CFR 890.105(b)(1) (eCFR as of 2026-11-24)"},
    {"match": r"(?i)^oxford", "kinds": None, "days": 365, "months": 12, "verified": True, "legal": {"months": 12},
     "label": "Oxford (UnitedHealthcare): 'file your reconsideration and/or appeal request of an administrative claim determination within "
              "12 months (or as required by law or your Agreement) from the date of the original EOB or PRA' (one window for both steps)",
     "source": "2026 UnitedHealthcare Administrative Guide, Oxford commercial supplement, Claim reconsideration (p. 336)"},
    {"match": NOT_MEDICARE_ITSELF, "kinds": {"medicare"}, "days": 120, "receipt_days": 5, "verified": False, "legal": {"days": 125},
     "label": "This payer is not Medicare itself, though its claims carry Medicare's filing indicator: its own window is not known, so "
              "Medicare's redetermination window is used as the shorter one (120 days from receipt of the remittance, 125 from its date)",
     "source": "42 CFR 405.942 as a stand-in; the payer's own rules not checked"},
]
# The payer's own limit where the due date shown is a safer, earlier one (the answer key compared each window with its
# document): Medicare's 120 days run from the receipt of the remittance, presumed 5 days after it (42 CFR 405.942: 125
# days from the remittance); CMS's 2026 guidance gives Medicare Advantage reconsiderations 65 days; a window in months or
# years ends on the same calendar day (18 months is not 540 days, two years across a leap day is 731). The safe date stays
# the item's due date; the legal one is shown beside it ("file by <due>; the payer's limit is <legal>").
APPEAL_LEGAL_DEFAULTS: dict[str, dict] = {"medicare": {"days": 125}, "medicare_advantage": {"days": 65}}
FEP_LEGAL = {"months": 6}           # federal employee plans: 6 months (5 CFR 890.105)
APPEAL_DEFAULTS: dict[str, tuple[int, str]] = {
    "medicare": (120, "Medicare Part B: redetermination within 120 days of receipt of the remittance (42 CFR 405.942), which is presumed "
                      "received 5 days after its date (405.942(a)(1)), so the date shown is 125 days after the remittance"),
    "medicare_advantage": (60, "Medicare Advantage: contracted providers follow the plan manual; 60 days is the conservative default"),
    "medicaid": (90, "Medicaid managed care: 90 days is the conservative default; check the plan manual"),
    "commercial": (180, "Commercial plans: 180 days is the common default (also the ERISA minimum for a member appeal)"),
    "supplement": (180, "Supplement plans: 180 days default"),
    "tricare": (90, "TRICARE: 90 days from the date of the EOB"),
    "va": (90, "VA Community Care: 90 days from the denial"),
    "other": (180, "Default 180 days"),
}
# 42 CFR 405.942(a)(1): a Medicare Part B redetermination is due 120 days from receipt of the notice of initial determination,
# and the notice (the remittance) is presumed received 5 calendar days after its date
MEDICARE_RECEIPT_DAYS = 5
APPEAL_DEFAULT_RECEIPT = {"medicare": MEDICARE_RECEIPT_DAYS}


# A claim fixed and sent again (a corrected claim, or for Medicare Part B a reopening) runs on its own clock, which is
# not the appeal window: some payers count from the date of service, others from the original remittance. Only rules
# read in the payer's own manual or the regulation are listed; for other payers the filing limit in the participation
# agreement applies, and the finding keeps the appeal window with a note saying so.
CORRECTED_RULES: list[dict] = [
    {"match": r"^Medicare|^Railroad Medicare", "kinds": {"medicare"}, "rarc": "MA130", "days": 365, "months": 12, "clock": "dos", "verified": True,
     "label": "Medicare Part B: a claim returned as unprocessable (MA130) is fixed by sending a new claim within 1 calendar year of the date of service (42 CFR 424.44)",
     "source": "42 CFR 424.44; WPS, How to correct a rejected claim"},
    {"match": r"^Medicare|^Railroad Medicare", "kinds": {"medicare"}, "clerical": True, "days": 365, "months": 12, "clock": "remit", "verified": True,
     "label": "Medicare Part B takes no corrected claims: ask for a reopening (a clerical error, including a wrong or missing modifier) within 1 year of the initial determination (42 CFR 405.980)",
     "source": "42 CFR 405.980(b); CMS Claims Processing Manual ch. 34 sec. 10.4; CGS reopening and redetermination job aid, December 2025"},
    {"match": r"^Medicare|^Railroad Medicare", "kinds": {"medicare"}, "days": 120, "receipt_days": MEDICARE_RECEIPT_DAYS, "clock": "remit",
     "verified": True, "legal": {"days": 125},
     "label": "Medicare Part B: information or records the claim lacked go in a redetermination within 120 days of receipt of the remittance "
              "(42 CFR 405.942; presumed received 5 days after its date, so 125 days after it); a reopening takes no documents",
     "source": "42 CFR 405.942; CGS reopening and redetermination job aid, December 2025 (documentation is not reviewed in a reopening)"},
    # The 'Claim Disputes and Corrected Claims' tip sheet this rule cited could not be found (the A17 research, A17 product
    # finding 19, and this unit: Humana's how-to page no longer carries it): the manual's claim limit dates the corrected
    # claim, not confirmed for one (fx-windows)
    {"match": r"^Humana", "kinds": {"medicare_advantage"}, "days": 365, "months": 12, "clock": "dos", "verified": False,
     "label": "Humana Medicare Advantage: a corrected claim within 1 year of the date of service, the manual's limit for a claim ('Medicare "
              "lines of business: 1 year from date of service', unless the agreement says otherwise). The manual sets no corrected-claim "
              "window of its own, and the tip sheet earlier cited for one was not found",
     "source": "Humana 2026 Provider Manual for physicians, hospitals and healthcare providers (773902ALL0725-A), claims submission"},
    {"match": r"^Humana", "kinds": {"medicaid"}, "days": 365, "clock": "dos", "verified": False,
     "label": "Humana Medicaid: corrected claim within 365 days of the date of service, the Kentucky plan's rule; the remittance does not say "
              "whether the member is in the Kentucky or the Indiana plan, and the Indiana plan's limit is not checked",
     "source": "Humana Healthy Horizons in Kentucky provider manual, 2025"},
    {"match": r"^Anthem", "kinds": {"medicare_advantage"}, "days": 365, "clock": "dos", "verified": True,
     "label": "Anthem Medicare Advantage: corrected claim within 365 days of the date of service, if the original claim was sent on time",
     "source": "Anthem Medicare Advantage provider guidebook (effective January 1, 2027); Anthem corrected claims reimbursement policy"},
    {"match": r"^Anthem", "kinds": {"medicaid"}, "state": "KY", "days": 365, "clock": "dos", "verified": True,
     "label": "Anthem Kentucky Medicaid: a corrected claim 'when received within the applicable timely filing requirements of the original "
              "claim': 365 days from the date of service",
     "source": "Anthem Blue Cross and Blue Shield Medicaid (Kentucky) Reimbursement Policy G-16001, Corrected Claims (last approved 08/28/2023)"},
    # A participating provider's FEP claims, corrected ones included, go within the agreement's filing limit (Anthem's provider
    # manual); the brochure's December 31 is the member's own deadline (deadlines reviews r1-1, r2-3)
    {"match": FEP_GROUP, "plan": FEP_PLAN, "kinds": None, "days": 90, "clock": "dos", "verified": False,
     "label": "Federal Employee Program (Anthem): a corrected claim goes within the filing limit in the participation agreement, counted from "
              "the date of service: FEP claims go 'within the timeframe listed in the Provider or Facility Agreement', and 'Any rejected "
              "Claims must be corrected and resubmitted within the timely filing period'. The agreement's number is not on file: the date "
              "shown is the common 90 days",
     "source": FEHBP_MANUAL + "; the same manual, Claims Filing Tips, Proof of Timely Filing"},
    # the 90 days is the guide's example of an agreement's limit, not the practice's own number (deadlines review r1-8)
    {"match": r"^(UnitedHealthcare|UHC)(?! Integrated)", "kinds": None, "days": 90, "clock": "dos", "verified": False,
     "label": "UnitedHealthcare: a corrected claim must reach it within the participation agreement's filing limit, counted from the date of "
              "service ('This number of days also applies to submission of corrected claims'). The agreement's number is not on file: "
              "the date shown uses the guide's own example, 90 days",
     "source": "2026 UnitedHealthcare Administrative Guide, chapter 10, Timely filing of corrected claims (PDF revised May 13, 2026)"},
    {"match": r"(?i)^oxford", "kinds": None, "days": 365, "months": 12, "clock": "remit", "verified": True,
     "label": "Oxford (UnitedHealthcare): corrected information goes in a reconsideration 'within 12 months (or as required by law or your "
              "Agreement) from the date of the original EOB or PRA'",
     "source": "2026 UnitedHealthcare Administrative Guide, Oxford commercial supplement, Claim reconsideration (p. 336)"},
    {"match": r"^WellCare", "kinds": {"medicaid"}, "state": "KY", "days": 365, "months": 12, "clock": "dos", "verified": True,
     "label": "WellCare of Kentucky Medicaid: 'Providers must submit claims (initialed, corrected and voided) within 12 months' of the date "
              "of service; corrected and voided claims 'are subject to timely claims submission'",
     "source": "WellCare of Kentucky 2025-2026 Kentucky Medicaid Provider Manual, Timely Claims Submission; Corrected or Voided Claims"},
    # crossover: a claim billed to Medicare first may also go within 6 months of Medicare's adjudication (907 KAR 3:005); detection
    # dates it where Medicare's remittance for the same bill and day is on file (deadlines review r1-5)
    {"match": r"^Medicaid KY$", "kinds": {"medicaid"}, "days": 365, "months": 12, "clock": "dos", "verified": True,
     "crossover": {"kinds": ("medicare",), "months": 6},
     "label": "Kentucky Medicaid (fee-for-service): a corrected or new claim, since 'Claims shall be submitted for payment within twelve (12) "
              "months of the date the service was rendered' (907 KAR 1:673); a claim billed to Medicare first may also go 'Within six (6) "
              "months of the Medicare adjudication date' if that is later (907 KAR 3:005)",
     "source": "907 KAR 1:673 Section 2(1)(b) (certified effective September 24, 2026); 907 KAR 3:005 Section 1 (amended effective July 16, 2026)"},
    # resubmit_denials: the program wants every denial its claim information caused corrected and sent again as a new claim
    # before an administrative review (IHCP, 'For Claim Denials'), so a denial's item stays workable inside this window
    # whatever its route (deadlines review r2-6)
    {"match": r"^Medicaid IN$", "kinds": {"medicaid"}, "paid": False, "days": 180, "clock": "dos", "verified": True, "resubmit_denials": True,
     "label": "Indiana Medicaid (fee-for-service): 'a denied claim that is resubmitted with corrected information is considered to be an "
              "initial claim and, as such, is subject to the 180-day timely filing limit', counted from the date of service; a denial "
              "caused by the claim's information is corrected and sent again before any administrative review",
     "source": "IHCP Provider Reference Module, Claim Submission and Processing (version 8.6, published November 24, 2026), Section 11; "
               "Claim Administrative Review and Appeals (March 24, 2026), For Claim Denials"},
    {"match": r"^Medicaid IN$", "kinds": {"medicaid"}, "paid": True, "days": 60, "clock": "remit", "verified": True,
     "label": "Indiana Medicaid (fee-for-service): a paid claim's adjustment or replacement 'must be filed within 60 days of notification of "
              "the claim's disposition' (the date on the RA)",
     "source": "IHCP Provider Reference Module, Claim Administrative Review and Appeals (March 24, 2026), Steps Taken Prior to the "
               "Administrative Review Process"},
    # not known whether the line was paid: the earlier of the two windows, 60 days from the RA or 180 days from the service
    # (corrected_window dates both; deadlines review r1-7)
    {"match": r"^Medicaid IN$", "kinds": {"medicaid"}, "days": 60, "clock": "remit", "or_dos": {"days": 180}, "verified": True,
     "label": "Indiana Medicaid (fee-for-service): a paid claim's adjustment or replacement 'must be filed within 60 days of notification of "
              "the claim's disposition' (the date on the RA); a denied claim corrected is a new claim within 180 days of the service. The "
              "remittance does not say which this line is, so the earlier of the two dates is used",
     "source": "IHCP Provider Reference Module, Claim Administrative Review and Appeals (March 24, 2026), Steps Taken Prior to the "
               "Administrative Review Process; Claim Submission and Processing (November 24, 2026), Section 11"},
    {"match": r"^Tricare", "kinds": {"tricare"}, "days": 365, "months": 12, "clock": "dos", "verified": True,
     "label": "TRICARE: a claim returned for more information goes back 'no later than the later of' one year after the service or 90 days "
              "from the date it was returned (32 CFR 199.7(d)(1))",
     "source": "32 CFR 199.7(d)(1) (eCFR as of 2026-11-24)"},
]
AGREEMENT_FILING_NOTE = (" For a corrected claim the filing limit in the participation agreement applies instead, counted from the date of "
                         "service; it can be much shorter (often 90 days) and is not confirmed here.")

# What a Medicare Part B reopening can fix: a clerical error, such as a wrong or missing modifier or code. Information or
# records the claim lacked go in a redetermination, because a reopening takes no documents.
MEDICARE_CLERICAL_SIGNALS = {"EM_BUNDLED_NO_MODIFIER", "EM_BUNDLED_MOD57_MINOR", "DENIED_MISSING_MODIFIER", "DENIED_CODING"}
MEDICARE_CLERICAL_CARCS = {"4", "5", "6", "11", "146", "181", "182", "199"}         # a whole claim denied for its codes or modifiers


def corrected_rule(payer_group: str | None, payer_kind: str | None, rarcs: str | None = None, signal: str | None = None,
                   carc_code: str | None = None, **facts) -> dict | None:
    """The clock for fixing and resending a claim to this payer: {'days', 'clock' ('dos' or 'remit'), 'label', 'source',
    'verified', ...}, or None when only the participation agreement says. `rarcs` are the remarks on the line and its claim;
    `signal` and the CARC (`carc_code`, or the facts' carc) say whether a Medicare fix is clerical (a reopening) or needs
    records (a redetermination). `facts` (rule_facts) tell one plan of the group from another: an Indiana Medicaid line that
    was paid is adjusted within 60 days of the remittance, a denied one is a new claim within 180 days of the service."""
    grp, kind = payer_group or "", payer_kind or "other"
    f = _facts(facts)
    grp, kind = _by_payer_id(grp, kind, f)
    carc = carc_code or f.get("carc")
    if carc and not f.get("carc"):
        f["carc"] = carc
    clerical = signal in MEDICARE_CLERICAL_SIGNALS or (signal == "CLAIM_DENIED_UNPAID" and (carc or "") in MEDICARE_CLERICAL_CARCS)
    for r in CORRECTED_RULES:
        if r.get("rarc") and r["rarc"] not in (rarcs or ""):
            continue
        if r.get("clerical") and not clerical:
            continue
        if _fits(r, grp, kind, f):
            return _public(r)
    return None


def appeal_rule(payer_group: str | None, payer_kind: str | None, plan: str | None = None, **facts) -> dict:
    """Filing window for the first-level dispute: {'days', 'label', 'source', 'verified', 'months', 'receipt_days', 'legal',
    'no_appeal', ...} (its last day: window_end; the payer's own limit: legal_deadline). `plan` (the 835's contract class)
    and `facts` (rule_facts: the payer's name, the denial's reason...) tell one plan of the group from another."""
    grp, kind = payer_group or "", payer_kind or "other"
    f = _facts(facts)
    grp, kind = _by_payer_id(grp, kind, f)
    if plan and not f.get("plan"):
        f["plan"] = plan
        if not facts.get("state"):
            f["state"] = plan_state(plan, f.get("payer_name"))
    for r in APPEAL_RULES:
        if _fits(r, grp, kind, f):
            return _public(r)
    days, label = APPEAL_DEFAULTS.get(kind, APPEAL_DEFAULTS["other"])
    return {"days": days, "label": label, "source": "default by payer type", "verified": kind == "medicare", "months": None, "year_end": None,
            "receipt_days": APPEAL_DEFAULT_RECEIPT.get(kind, 0), "legal": APPEAL_LEGAL_DEFAULTS.get(kind), "since": None, "before": None,
            "no_appeal": False, "no_appeal_step": None, "cob": None}


# --------------------------------------------------------------------------- calendar arithmetic
def add_months(d: date, months: int) -> date:
    """The same day of the month `months` calendar months on, or that month's last day when it has no such day
    (18 months from 2025-08-31 is 2027-02-28; 12 months from 2028-02-29 is 2029-02-28)."""
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


_add_months = add_months           # the name legal_deadline's callers knew


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The n-th `weekday` (Monday 0) of a month; n = -1 is the last one."""
    if n > 0:
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    last = date(year, month, calendar.monthrange(year, month)[1])
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def federal_holidays(year: int) -> frozenset:
    """The legal public holidays of 5 U.S.C. 6103(a) in `year`, and the workday observed in place of one that falls on a
    weekend (the Friday before a Saturday holiday, the Monday after a Sunday one: 5 U.S.C. 6103(b), Executive Order 11582).
    New Year's Day of the next year observed on December 31 is in the set too."""
    days = [date(year, 1, 1), _nth_weekday(year, 1, 0, 3), _nth_weekday(year, 2, 0, 3), _nth_weekday(year, 5, 0, -1),
            date(year, 6, 19), date(year, 7, 4), _nth_weekday(year, 9, 0, 1), _nth_weekday(year, 10, 0, 2), date(year, 11, 11),
            _nth_weekday(year, 11, 3, 4), date(year, 12, 25)]
    out = set(days)
    for d in days:
        if d.weekday() == 5:
            out.add(d - timedelta(days=1))
        elif d.weekday() == 6:
            out.add(d + timedelta(days=1))
    if date(year + 1, 1, 1).weekday() == 5:
        out.add(date(year, 12, 31))
    return frozenset(out)


def next_workday(d: date) -> date:
    """`d`, or the next day that is no Federal nonworkday (a Saturday, a Sunday, a legal public holiday or the workday
    observed in its place): the last day of a period that falls on a nonworkday moves to the next workday (42 CFR 424.44(c)).
    A day the President closes Federal offices on by a one-time order cannot be known in advance and is not counted."""
    while d.weekday() >= 5 or d in federal_holidays(d.year):
        d += timedelta(days=1)
    return d


def _variant(start: str, rule: dict) -> dict:
    """The rule a window that opens on `start` runs by: one that applies from an effective date ('since') gives way to the
    rule before it ('before') for an earlier start."""
    since = rule.get("since")
    if since and rule.get("before") and start < since:
        return rule["before"]
    return rule


def _day(start) -> date | None:
    try:
        return date.fromisoformat(str(start)[:10]) if start else None
    except ValueError:
        return None


def legal_deadline(start: str | None, rule: dict | None) -> str | None:
    """The payer's own limit for a window that starts on `start` (ISO date), from the rule's 'legal' spec ({'days': n},
    {'months': n}, {'years': n} or {'year_end': n}, December 31 of the n-th year after the start's; with 'next_workday',
    a last day on a Federal nonworkday moves to the next workday); None when the rule has none (the due date shown is the
    payer's own limit)."""
    d = _day(start)
    if d is None or not rule:
        return None
    spec = _variant(d.isoformat(), rule).get("legal")
    if not spec:
        return None
    if spec.get("days"):
        out = d + timedelta(days=int(spec["days"]))
    elif spec.get("year_end"):
        out = date(d.year + int(spec["year_end"]), 12, 31)
    else:
        months = int(spec.get("months") or 0) + 12 * int(spec.get("years") or 0)
        if not months:
            return None
        out = add_months(d, months)
    if spec.get("next_workday"):
        out = next_workday(out)
    return out.isoformat()


# --------------------------------------------------------------------------- first filing
# The limit for a claim's first filing, counted from the date of service: a claim the payer has no record of (never
# answered, or rejected before it reached the payer) must reach it by then. The rules below were read in the regulation or
# the payer's own manual; every other limit is the common one for the kind of payer and is not confirmed: the
# participation agreement's own limit applies, and it can be shorter.
# "cob" says how a payer counts a claim it gets as the second payer, where its source says (second_claim_window): "dos",
# from the date of service even then (Medicare, CHAMPVA and TRICARE grant a delay by the first payer only as an exception on
# request; GEHA's brochure counts from the year of service); "remit", from the first payer's explanation of benefits
# (Ambetter's coordination of benefits, Anthem's FEP). Where the source is silent, the safe date runs from the date of
# service and the last day from the first payer's remittance, so a guess never makes a second payer's share a loss early.
FILING_RULES: list[dict] = [
    {"match": r"^Medicare|^Railroad Medicare", "kinds": {"medicare"}, "days": 365, "months": 12, "verified": True, "cob": "dos",
     "legal": {"months": 12, "next_workday": True},
     "label": "Medicare Part B: 'no later than the close of the period ending 1 calendar year after the date of service' (42 CFR "
              "424.44(a)(1)); a last day on a Saturday, Sunday or Federal holiday moves to the next workday (424.44(c))",
     "source": "42 CFR 424.44(a)(1) and (c) (eCFR as of 2026-11-24)"},
    {"match": r"^Humana", "kinds": {"medicare_advantage"}, "days": 365, "months": 12, "verified": True,
     "label": "Humana Medicare Advantage: 1 year from the date of service (Humana Provider Manual 2026, claims submission)",
     "source": "Humana Provider Manual 2026"},
    {"match": r"^WellCare", "kinds": {"medicare_advantage"}, "days": 180, "verified": True,
     "label": "Wellcare Medicare Advantage: 'Timely Filing guidelines: 180 days from date of service' (its Kentucky Medicare Quick Reference "
              "Guide, January 2026)",
     "source": "Wellcare, Kentucky Medicare Quick Reference Guide, January 2026"},
    # the payer rules do not know CHAMPVA's names ("CHAMPVA", "CHAMP VA - HAC", "CHAMPVA-HAC MEDICARE CROSSOVER"): their group is the name
    {"match": r"(?i)^champ\s*-?\s*va", "kinds": None, "days": 365, "months": 12, "verified": True, "cob": "dos",
     "label": "CHAMPVA: 'One year after the date of service' (38 CFR 17.276(a)(1)); a first payer's delay is an exception only on a written "
              "request (17.276(b))", "source": "38 CFR 17.276(a)(1) and (b)"},
    {"match": r"^Tricare", "kinds": {"tricare"}, "days": 365, "months": 12, "verified": True, "cob": "dos",
     "label": "TRICARE: claims 'no later than one year after the services are provided' (32 CFR 199.7(d)); a delay by another health "
              "insurance is an exception only on request (199.7(d)(2))",
     "source": "32 CFR 199.7(d) (eCFR as of 2026-11-24)"},
    # Anthem's provider manual, not the member's brochure, sets a participating provider's FEP limit (deadlines reviews r1-1, r2-3)
    {"match": FEP_GROUP, "plan": FEP_PLAN, "kinds": None, "days": 90, "verified": False, "cob": "remit",
     "label": "Federal Employee Program (Anthem): 'All Claims under the FEHBP and PSHBP must be submitted to the Plan for payment within the "
              "timeframe listed in the Provider or Facility Agreement', counted 'from the date of the primary payer's explanation of "
              "benefits' where another payer is first. The agreement's number is not on file: the date shown is the common 90 days for a "
              "participating provider. The brochure's December 31 of the year after the service is the member's own deadline",
     "source": FEHBP_MANUAL + "; 2026 Service Benefit Plan brochure RI 71-005, Section 7"},
    # GEHA's brochure speaks to the member and no GEHA provider source was read: its December 31 is the latest the plan takes
    # any claim, used to date the item so a guess never expires it, and the 90 days a network agreement often sets is named
    {"match": GEHA_MATCH, "kinds": None, "days": 365, "year_end": 1, "verified": False, "earliest": 90, "cob": "dos",
     "label": "GEHA (Federal Employees Health Benefits): its brochure's 'You must submit the claim by December 31 of the year after the year "
              "you received the service' is the member's own deadline and the latest the plan takes a claim; a network provider's own "
              "limit is in its agreement, which is not on file, and it can be 90 days",
     "source": "2026 GEHA Benefit Plan brochure RI 71-006, Section 7, Deadline for filing your claim"},
    {"match": r"^Ambetter", "kinds": None, "days": 90, "verified": True, "legal": {"days": 180}, "cob": "remit",
     "label": "Ambetter: initial claims within 180 calendar days of the date of service for a participating provider and 90 for a "
              "non-participating one (its Kentucky and Indiana 2026 manuals); the date shown is 90 days, the participating limit 180. As "
              "the second payer: 180 (90) days 'from the date of Explanation of Payment from the primary payers'",
     "source": "Ambetter from WellCare of Kentucky Provider and Billing Manual 2026; Ambetter (Indiana) Provider and Billing Manual 2026, Timely Filing"},
    # Anthem's Medicaid plans count a claim they get as the second payer from the other payer's answer: Kentucky's 'time frames
    # for filing a claim will begin on the date of the third party's resolution of the claim', Indiana's 90 days run 'between
    # the other payer's remittance advice (RA) date and the Anthem receipt date' (second_claim_window; fx-windows)
    {"match": r"^Anthem", "kinds": {"medicaid"}, "state": "KY", "days": 365, "verified": True, "cob": "remit",
     "label": "Anthem Kentucky Medicaid: claims 'must be received within 365 calendar days of the date of discharge for inpatient services "
              "and date of service for outpatient services'; as the second payer, 'the time frames for filing a claim will begin on the "
              "date of the third party's resolution of the claim'",
     "source": "Anthem Blue Cross and Blue Shield Kentucky Medicaid Provider Manual, 11.7 Timely Filing"},
    {"match": r"^Anthem", "kinds": {"medicaid"}, "state": "IN", "days": 90, "verified": True, "cob": "remit",
     "label": "Anthem Indiana Medicaid: 'Participating providers have 90 calendar days from the date of service' (nonparticipating ones too); "
              "as the second payer, '90 calendar days between the other payer's remittance advice (RA) date and the Anthem receipt date'",
     "source": "Anthem Blue Cross and Blue Shield (Indiana Medicaid), IHCP Works 2026: Top denials and quick tips, October 2026"},
    {"match": r"^Anthem", "kinds": {"medicaid"}, "other_state": ("KY", "IN"), "days": 90, "verified": False,
     "label": "Anthem Medicaid: the remittance names a plan of another state than Kentucky or Indiana; that plan's limit is not checked, "
              "so Indiana's shorter one is used: 90 calendar days from the date of service",
     "source": "Anthem (Indiana Medicaid), IHCP Works 2026: Top denials and quick tips (the other state's plan not checked)"},
    {"match": r"^Anthem", "kinds": {"medicaid"}, "days": 90, "verified": True, "cob": "remit",
     "label": "Anthem Medicaid: the remittance does not say whether this is Anthem's Kentucky or its Indiana Medicaid plan, so the shorter "
              "limit is used: Indiana's 90 calendar days from the date of service (Kentucky's: 365 days); as the second payer, both count "
              "from the other payer's answer",
     "source": "Anthem (Indiana Medicaid), IHCP Works 2026: Top denials and quick tips; Anthem Kentucky Medicaid Provider Manual"},
    # the second payer's alternative is the later of the two dates (second_claim_window's 'later'; fx-windows)
    {"match": r"^WellCare", "kinds": {"medicaid"}, "state": "KY", "days": 365, "months": 12, "verified": True, "cob": {"later": {"months": 6}},
     "label": "WellCare of Kentucky Medicaid: 'Providers must submit claims (initialed, corrected and voided) within 12 months (or six months "
              "from the Medicaid or primary insurance payment date, whichever is later) from the date of service'",
     "source": "WellCare of Kentucky 2025-2026 Kentucky Medicaid Provider Manual, Timely Claims Submission"},
    # Indiana Medicaid's managed care plans (Anthem's has its rows above): the program's bulletin sets a plan's limit for a
    # provider in its network, and for one out of it; whether the practice is in each plan's network is not on file, so the
    # in-network 90 days is the date shown and the out-of-network 180 the last day (fx-windows)
    {"match": r"^(?!Medicaid (?:IN|KY)$)", "kinds": {"medicaid"}, "state": "IN", "days": 90, "verified": True, "legal": {"days": 180},
     "label": "Indiana Medicaid managed care plan: 'the timely filing limit on claims for services rendered to members enrolled in managed "
              "care by in-network providers' is 90 days, and 'The managed care claim timely filing limit for out-of-network providers also "
              "remains at 180 calendar days'. The date shown is 90 days from the date of service; the last day, 180, is for a provider "
              "outside the plan's network",
     "source": "IHCP bulletin BT202072 (June 11, 2020), Managed care claim timely filing returns to 90 days"},
    {"match": r"^Medicaid IN$", "kinds": {"medicaid"}, "days": 180, "verified": True,
     "label": "Indiana Medicaid (fee-for-service): claims 'must be originally filed within 180 days of the date of service'; a crossover "
              "claim Medicare or a Medicare Advantage plan paid is not held to it, and a primary payer's late notice moves it to 180 "
              "days from that payer's EOB",
     "source": "IHCP Provider Reference Module, Claim Administrative Review and Appeals (March 24, 2026), Introduction; Claim Submission "
               "and Processing (November 24, 2026), Section 11"},
    {"match": r"^Medicaid KY$", "kinds": {"medicaid"}, "days": 365, "months": 12, "verified": True,
     "label": "Kentucky Medicaid (fee-for-service): 'Claims shall be submitted for payment within twelve (12) months of the date the service "
              "was rendered' (907 KAR 1:673); a claim billed to Medicare first may also go 'Within six (6) months of the Medicare "
              "adjudication date' if that is later (907 KAR 3:005)",
     "source": "907 KAR 1:673 Section 2(1)(b); 907 KAR 3:005 Section 1"},
    {"match": r"", "cfi": {"WC"}, "kinds": None, "days": 45, "verified": True,
     "label": "Workers' compensation (Kentucky): bills are due within 45 days of the treatment (KRS 342.020; 803 KAR 25:096); an Indiana "
              "claim's limit is not checked, so Kentucky's is used",
     "source": "803 KAR 25:096 Section 6 and KRS 342.020"},
    # the VA's community care networks, by their group or (filing_rule's _by_payer_id) by the payer id a claim carries
    {"match": r"^VA Community Care$", "kinds": {"va"}, "days": 180, "verified": True,
     "label": "VA Community Care Network (38 U.S.C. 1703D(b)): a provider 'shall submit to the Secretary a claim for payment for furnishing the hospital care, "
              "medical service, or extended care service not later than 180 days after the date on which the entity or provider furnished' it",
     "source": "38 U.S.C. 1703D(b), Prompt payment standard (VA community care)"},
    {"match": r"(?i)^gallagher bassett", "kinds": None, "days": 45, "verified": True,
     "label": "Workers' compensation (Kentucky): bills are due within 45 days of the treatment (KRS 342.020; 803 KAR 25:096); an Indiana "
              "claim's limit is not checked, so Kentucky's is used",
     "source": "803 KAR 25:096 Section 6 and KRS 342.020"},
    {"match": r"(?i)^oxford", "kinds": None, "days": 365, "months": 12, "verified": False, "earliest": 90,
     "label": "Oxford (UnitedHealthcare): the participation agreement sets the limit ('Refer to your internal contracting contact or "
              "Participation Agreement for timely filing information'), and it is not on file. The date shown is the longest common "
              "limit, 1 year from the date of service; it can be 90 days",
     "source": "2026 UnitedHealthcare Administrative Guide, front contact table"},
    {"match": NOT_MEDICARE_ITSELF, "kinds": {"medicare"}, "days": 365, "months": 12, "verified": False, "earliest": 90,
     "label": "This payer is not Medicare itself, though its claims carry Medicare's filing indicator: its limit is not known. Common "
              "limits run from 90 days to 1 year from the date of service; the date shown is the longest, and it can be 90 days",
     "source": "common limit for an unknown payer"},
]
FILING_DEFAULTS: dict[str, tuple[int, str]] = {
    "medicare": (365, "Medicare: 1 calendar year from the date of service (42 CFR 424.44)"),
    "medicare_advantage": (365, "Medicare Advantage: 1 year from the date of service is the common limit; a contracted plan's agreement can set less"),
    "medicaid": (365, "Medicaid: 365 days from the date of service is the common limit; check the plan"),
    "commercial": (90, "Commercial plans: 90 days from the date of service is the common limit for participating providers; the agreement's own limit applies"),
    "supplement": (365, "Supplement plans: claims normally follow Medicare's, so 1 year from the date of service"),
    "tricare": (365, "TRICARE: 1 year from the date of service"),
    "va": (180, "VA Community Care Network: 180 days from the date of service"),
    # A payer the rules do not know: its item is dated by the longest common limit, so a guess never makes it expire, and
    # the item also names the shortest one (FILING_EARLIEST) as the date to send it by where possible.
    "other": (365, "This payer's limit is not known: common limits run from 90 days to 1 year from the date of service. The date "
                   "shown is the longest; the agreement's own limit applies, and it can be 90 days"),
}
FILING_EARLIEST: dict[str, int] = {"other": 90}
# the defaults stated in years ("1 calendar year", "1 year"), counted in calendar months; the others are stated in days
FILING_DEFAULT_MONTHS: dict[str, int] = {"medicare": 12, "medicare_advantage": 12, "supplement": 12, "tricare": 12, "other": 12}


def filing_rule(payer_group: str | None, payer_kind: str | None, **facts) -> dict:
    """The first-filing limit for this payer: {'days', 'label', 'source', 'verified', 'earliest', 'months', 'legal', ...},
    counted from the date of service. 'earliest' is set only where the limit is not known: the shortest common limit, to
    send the claim by where possible, while 'days' is the longest, which dates the item. `facts` (rule_facts: the plan, the
    payer's name, the claim filing indicator) tell one plan of the group from another."""
    grp, kind = payer_group or "", payer_kind or "other"
    f = _facts(facts)
    grp, kind = _by_payer_id(grp, kind, f)
    for r in FILING_RULES:
        if _fits(r, grp, kind, f):
            return _public(r, earliest=r.get("earliest"))
    days, label = FILING_DEFAULTS.get(kind, FILING_DEFAULTS["other"])
    known = kind if kind in FILING_DEFAULTS else "other"
    return {"days": days, "label": label, "source": "common limit for this kind of payer", "verified": kind == "medicare",
            "earliest": FILING_EARLIEST.get(known), "months": FILING_DEFAULT_MONTHS.get(known), "year_end": None, "receipt_days": 0,
            "legal": None, "since": None, "before": None, "no_appeal": False, "no_appeal_step": None, "cob": None}


def window_end(start: str | None, rule: dict) -> str | None:
    """The last day of a window that opens on `start` (YYYY-MM-DD), as the rule states it: in calendar months where the
    payer states months or years (Humana's 18 months, Anthem's two years, a calendar year from the date of service), on
    December 31 where the window runs to the end of a later year (a federal employee plan's claim), in days where it states
    days; counted from the day a notice is presumed received where the rule says so (receipt_days: Medicare's 5 days, 42 CFR
    405.942(a)(1)); by the rule before an effective date for an earlier start. None without a date it can read."""
    d = _day(start)
    if d is None or not rule:
        return None
    rule = _variant(d.isoformat(), rule)
    d += timedelta(days=int(rule.get("receipt_days") or 0))
    if rule.get("year_end"):
        return date(d.year + int(rule["year_end"]), 12, 31).isoformat()
    months = rule.get("months")
    return (add_months(d, int(months)) if months else d + timedelta(days=int(rule["days"]))).isoformat()


def rule_at(start: str | None, rule: dict | None) -> dict | None:
    """The rule a window that opens on `start` was dated by, with its own label and verified flag: for a window that applies
    from an effective date (KRS 205.534's 120 days since April 14, 2026), the rule before it for an earlier start. An item
    names the rule that decided its date (deadlines review r2-5)."""
    if not rule:
        return rule
    d = _day(start)
    return _variant(d.isoformat(), rule) if d is not None else rule


def corrected_window(rule: dict | None, service_date: str | None, remit_date: str | None) -> tuple[str | None, str | None]:
    """(the safe date, the payer's own last day where later) of a corrected-claim rule (corrected_rule), from its clock: the
    date of service ('dos') or the remittance ('remit'), the other when one is missing. A rule with 'or_dos' (Indiana
    Medicaid, where the remittance does not say whether the line was paid) runs to the earlier of its own window and the
    one from the date of service it names: the shorter is used (deadlines review r1-7)."""
    if not rule:
        return None, None
    start = (service_date if rule.get("clock") == "dos" else remit_date) or remit_date or service_date
    safe, legal = window_end(start, rule), legal_deadline(start, rule)
    alt = rule.get("or_dos")
    if alt and service_date:
        other = window_end(service_date, alt)
        if other and (not safe or other < safe):
            safe = other
    return safe, (legal if legal and safe and legal > safe else None)


def second_claim_window(rule: dict | None, service_date: str | None, first_answer: str | None,
                        first_payer: str | None = None) -> tuple[str | None, str | None, str]:
    """A second payer's limit for a share the first payer left, from the second payer's first-filing rule (filing_rule):
    (the safe date, the payer's own last day where later, how they are counted, in words). The rule's 'cob' says how the
    payer counts a claim it gets as the second payer: 'dos' from the date of service (Medicare, CHAMPVA, TRICARE, GEHA's
    brochure), 'remit' from the first payer's remittance (Ambetter's coordination of benefits, Anthem's FEP and Medicaid
    plans), {'later': window} the later of its own window from the date of service and that window from the first payer's
    payment (WellCare of Kentucky's Medicaid plan: 12 months, 'or six months from the Medicaid or primary insurance payment
    date, whichever is later'). Where its source does not say, the safe date runs from the date of service and the last day
    from the first payer's remittance, so a guess never makes the share a loss early (deadlines review r1-2)."""
    if not rule:
        return None, None, ""
    cob = rule.get("cob")
    svc = service_date or first_answer
    rem = first_answer or service_date
    whose = f"{first_payer}'s remittance" if first_payer else "the first payer's remittance"
    if isinstance(cob, dict) and cob.get("later"):
        # both dates are the payer's own limit, whichever is later: no later last day (fx-windows)
        own = window_end(svc, rule)
        alt = window_end(first_answer, cob["later"]) if first_answer else None
        safe = max((d for d in (own, alt) if d), default=None)
        n = cob["later"].get("months")
        span = f"{n} months" if n else f"{cob['later'].get('days')} days"
        payer = f"{first_payer}'s" if first_payer else "the first payer's"
        how = (f"the later of the payer's own window from the date of service ({svc}) and {span} from {payer} payment ({first_answer})"
               if first_answer else f"counted from the date of service ({svc}): the first payer's payment date is not known")
        return safe, None, how
    if cob == "remit":
        safe, last = window_end(rem, rule), legal_deadline(rem, rule)
        how = f"counted from {whose} ({rem})"
    elif cob == "dos":
        safe, last = window_end(svc, rule), legal_deadline(svc, rule)
        how = f"counted from the date of service ({svc})"
    else:
        safe = window_end(svc, rule)
        last = max((d for d in (window_end(rem, rule), legal_deadline(rem, rule), legal_deadline(svc, rule)) if d), default=None)
        how = (f"the safe date counted from the date of service ({svc}), the last day from {whose} ({rem}): how this payer counts a "
               "second claim is not known")
    return safe, (last if last and safe and last > safe else None), how


# --------------------------------------------------------------------------- statutes cited in letters
# Wording checked against the Kentucky Legislative Research Commission statute PDFs and the Indiana Code.
LEGAL = {
    "KRS_702": "KRS 304.17A-702 requires an insurer to pay, deny or contest a clean claim within 30 calendar days of receipt.",
    "KRS_708": "KRS 304.17A-708(1) provides that an insurer shall not require a provider to appeal errors in payment where the insurer "
               "has not paid the claim according to the contracted rate, and that miscalculations shall be corrected and paid within "
               "30 calendar days of receipt of documentation verifying the error.",
    "KRS_730": "KRS 304.17A-730 requires interest on the unpaid amount of a clean claim at 12 percent per year when paid 1 to 30 days "
               "late, 18 percent when 31 to 60 days late and 21 percent when more than 60 days late, added without a request from the provider.",
    "KRS_714": "KRS 304.17A-714 requires written notice of an overpayment within 24 months of payment, with the covered person, date of "
               "service, claim reference and the basis for the determination, allows the provider 30 calendar days to dispute it, and bars "
               "recoupment until a timely dispute is resolved.",
    "IC_6_5": "Indiana Code 27-8-5.7-6.5 (and 27-13-36.2-4.5 for health maintenance organizations) provides that an insurer may not alter "
              "the CPT code submitted on a clean claim, or pay for a CPT code of lesser monetary value, unless the code submitted is not in "
              "accordance with coding guidelines, clinical care guidelines or the provider contract, or an employee or contractor of the "
              "insurer has reviewed the medical record.",
    "ERISA": "If this plan is an employer self-funded plan governed by ERISA, we request, as the member's authorized representative under "
             "the assignment of benefits, a copy of the specific rule, guideline, protocol or edit relied on for this determination "
             "(29 CFR 2560.503-1(g) and (h)).",
    "MA": "Medicare Advantage organizations must provide the basic benefits of original Medicare and follow its coverage and coding rules "
          "(42 CFR 422.101).",
    "MEDICARE": "This is a request for redetermination under 42 CFR 405.940 et seq., submitted within 120 days of receipt of the notice of "
                "initial determination (42 CFR 405.942(a)).",
}

# Medicare same-day visit policy, CY 2027 physician fee schedule final rule (CMS-1848-F; CMS fact sheet November 4, 2026;
# Federal Register November 9, 2026): when a separately identifiable office visit is furnished on the same day as a 0-day
# global procedure, the most expensive service (procedure or visit) is paid at 100 percent and every other service that
# day at 80 percent ("all services except the highest value service", a 10- or 90-day procedure on the claim included).
# Only a 0-day procedure brings the rule in: the proposed 50 percent and the 10- and 90-day triggers were not finalized.
MEDICARE_SAME_DAY_EM = {"effective": "2027-01-01", "pct": 0.80, "confirmed": True}


FIXED_KIND_GROUPS = frozenset({"Ambetter (Centene)"})


def payer_group_kind(name: str | None, cfi: str | None) -> tuple[str, str]:
    """Payer group and kind for a claim: the payer name rules refined by the claim filing indicator (CLP06).
    Medicare Advantage and Medicaid products that share a commercial payer's name get their own group."""
    grp, kind = normalize_payer(name)
    if grp in FIXED_KIND_GROUPS:
        return grp, kind            # a plan whose kind its claim filing indicator misreports
    refined = payer_kind_from_cfi(cfi, kind)
    if refined == "medicare" and kind in ("supplement", "commercial", "medicare_advantage", "medicaid"):
        refined = kind          # supplement and commercial carriers sometimes label their claims MB; they are not Medicare itself
    kind = refined
    if kind == "medicare_advantage" and "Medicare Advantage" not in grp:
        grp = f"{grp} Medicare Advantage"
    elif kind == "medicaid" and "Medicaid" not in grp:
        grp = f"{grp} Medicaid"
    return grp, kind


def carc_text(code: str | None) -> str:
    return CARC.get(code or "", "")


def rarc_text(code: str | None) -> str:
    return RARC.get(code or "", "")
