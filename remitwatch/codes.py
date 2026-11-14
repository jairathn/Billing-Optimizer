"""
Reference tables: CARC/RARC meanings, dermatology code attributes, payer normalization.

CARC and RARC texts are abbreviated from the X12 code lists
(https://x12.org/codes). Keep them short; the dashboard shows them as hints.
"""
from __future__ import annotations

import re

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
    (r"KY MEDICAID|KENTUCKY MEDICAID", "Medicaid KY", "medicaid"),
    (r"IN MEDICAID|INDIANA MEDICAID", "Medicaid IN", "medicaid"),
    (r"MEDICAID", "Medicaid (other)", "medicaid"),
    (r"HUMANA CARE PLAN|HUMANA.*(MEDICARE|ADVANTAGE|GOLD)", "Humana Medicare Advantage", "medicare_advantage"),
    (r"^HUMANA", "Humana", "commercial"),
    (r"AARP.*(ADVANTAGE)|UNITED.*MEDICARE ADV|UHC.*MEDICARE|AARP MEDICARE COMPLETE", "UHC Medicare Advantage", "medicare_advantage"),
    (r"AARP.*SUPPLEMENT|AARP MEDICARE SUPPLEMENT", "AARP Medicare Supplement (UHC)", "supplement"),
    (r"UNITED HEALTHCARE OF ALL STATES|^UHC$|UNITEDHEALTHCARE|UNITED HEALTHCARE$|UNITED HEALTH CARE", "UnitedHealthcare", "commercial"),
    (r"UNITEDHEALTH INTEGRATED|WAUSAU", "UHC Integrated Services (Wausau)", "commercial"),
    (r"UNITED MEDICAL RESOURCES|^UMR\b", "UMR", "commercial"),
    (r"OPTUM", "Optum (UHC)", "commercial"),
    (r"SUREST|BIND", "Surest (UHC)", "commercial"),
    (r"ANTHEM.*(MEDICARE|ADVANTAGE)|BLUE.*MEDICARE ADV|BCBS.*MEDICARE", "Anthem Medicare Advantage", "medicare_advantage"),
    (r"^ANTHEM|BLUE CROSS|BLUE SHIELD|^BCBS|BLUECARD|WELLPOINT|ELEVANCE", "Anthem / BCBS", "commercial"),
    (r"AETNA.*(MEDICARE|ADVANTAGE)", "Aetna Medicare Advantage", "medicare_advantage"),
    (r"AETNA.*AMERICAN CONTINENTAL|AMERICAN CONTINENTAL", "Aetna Senior Supplemental (American Continental)", "supplement"),
    (r"^AETNA", "Aetna", "commercial"),
    (r"CIGNA.*(MEDICARE|SUPPLEMENT)", "Cigna Medicare Supplement", "supplement"),
    (r"^CIGNA", "Cigna", "commercial"),
    (r"WELLCARE|AMBETTER", "WellCare / Ambetter (Centene)", "medicare_advantage"),
    (r"TRICARE", "Tricare", "tricare"),
    (r"VETERAN|^VA\b|TRIWEST|OPTUM VA|CCN", "VA Community Care", "va"),
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


# --------------------------------------------------------------------------- appeal windows
# Days from the remittance (EOP) date to file the first-level dispute, per the payer's published provider
# manual. `verified` is True only where the wording was read in the primary source during the build
# (docs/RESEARCH_SUMMARY.md lists the sources). A participation agreement can set a different period,
# so every deadline shown by the dashboard is an estimate.
APPEAL_RULES: list[dict] = [
    {"match": r"^Anthem / BCBS$", "kinds": {"commercial", "supplement", "other"}, "days": 730, "verified": True,
     "label": "Anthem commercial: Claim Payment Reconsideration within two years of the EOP issue date; "
              "Claim Payment Appeal within 90 days of the reconsideration decision",
     "source": "Anthem Blue Cross and Blue Shield Kentucky Commercial Provider Manual, effective September 1, 2026, Claim Payment Disputes"},
    {"match": r"^Anthem", "kinds": {"medicare_advantage"}, "days": 120, "verified": True,
     "label": "Anthem Medicare Advantage: reconsideration within 120 days of the date on the EOP; appeal within 30 days of the reconsideration letter",
     "source": "Anthem Medicare Advantage provider guidebook, effective January 1, 2027 (linked from the Kentucky and Indiana manual pages)"},
    {"match": r"^Anthem", "kinds": {"medicaid"}, "days": 90, "verified": True,
     "label": "Anthem Kentucky Medicaid: payment appeal within 90 calendar days of the remittance date",
     "source": "Anthem Kentucky Medicaid Provider Manual, payment appeals"},
    {"match": r"^(UnitedHealthcare|UHC)(?! Integrated)", "kinds": None, "days": 365, "verified": True,
     "label": "UnitedHealthcare: reconsideration and appeal together within 12 months of the original EOB or PRA (one window for both steps)",
     "source": "UnitedHealthcare Administrative Guide 2026, chapter 10 (revised May 13, 2026); uhcprovider.com appeals page"},
    {"match": r"^(UMR|Optum|Surest|UHC Integrated)", "kinds": None, "days": 365, "verified": False,
     "label": "UnitedHealthcare-administered plan: 12 months from the original EOB or PRA, as for UnitedHealthcare",
     "source": "UnitedHealthcare Administrative Guide 2026 (the plan's own rules not checked)"},
    {"match": r"^Humana", "kinds": {"medicaid"}, "days": 120, "verified": False,
     "label": "Humana Medicaid: 120 days used. Humana Healthy Horizons in Kentucky takes an appeal of a denial or reduction within 120 days "
              "(KRS 205.534) and a contracted-rate dispute within 24 months; the Indiana plan allows 60 days. The remittance does not say which plan",
     "source": "Humana Healthy Horizons in Kentucky appeals page (2026) and KRS 205.534; Humana Healthy Horizons in Indiana provider manual (2026)"},
    {"match": r"^Humana", "kinds": None, "days": 540, "verified": True,
     "label": "Humana: claim dispute within 18 months of receiving the original claim determination (counted here from the remittance date)",
     "source": "Humana Provider Manual 2026 (773902ALL0725), claims disputes, for all products"},
    {"match": r"^Aetna", "kinds": None, "days": 180, "verified": False,
     "label": "Aetna: reconsideration within 180 days of the claim decision; appeal within 60 days of the reconsideration decision",
     "source": "Aetna provider dispute page (as reported in research)"},
    {"match": r"^Cigna", "kinds": None, "days": 180, "verified": False,
     "label": "Cigna: payment review within 180 days of the initial payment or denial",
     "source": "Cigna reference guide (as reported in research)"},
]
APPEAL_DEFAULTS: dict[str, tuple[int, str]] = {
    "medicare": (120, "Medicare Part B: redetermination within 120 days of receipt of the remittance (42 CFR 405.942)"),
    "medicare_advantage": (60, "Medicare Advantage: contracted providers follow the plan manual; 60 days is the conservative default"),
    "medicaid": (90, "Medicaid managed care: 90 days is the conservative default; check the plan manual"),
    "commercial": (180, "Commercial plans: 180 days is the common default (also the ERISA minimum for a member appeal)"),
    "supplement": (180, "Supplement plans: 180 days default"),
    "tricare": (90, "TRICARE: 90 days from the date of the EOB"),
    "va": (90, "VA Community Care: 90 days from the denial"),
    "other": (180, "Default 180 days"),
}


# A claim fixed and sent again (a corrected claim, or for Medicare Part B a reopening) runs on its own clock, which is
# not the appeal window: some payers count from the date of service, others from the original remittance. Only rules
# read in the payer's own manual or the regulation are listed; for other payers the filing limit in the participation
# agreement applies, and the finding keeps the appeal window with a note saying so.
CORRECTED_RULES: list[dict] = [
    {"match": r"^Medicare|^Railroad Medicare", "kinds": {"medicare"}, "rarc": "MA130", "days": 365, "clock": "dos", "verified": True,
     "label": "Medicare Part B: a claim returned as unprocessable (MA130) is fixed by sending a new claim within 1 calendar year of the date of service (42 CFR 424.44)",
     "source": "42 CFR 424.44; WPS, How to correct a rejected claim"},
    {"match": r"^Medicare|^Railroad Medicare", "kinds": {"medicare"}, "clerical": True, "days": 365, "clock": "remit", "verified": True,
     "label": "Medicare Part B takes no corrected claims: ask for a reopening (a clerical error, including a wrong or missing modifier) within 1 year of the initial determination (42 CFR 405.980)",
     "source": "42 CFR 405.980(b); CMS Claims Processing Manual ch. 34 sec. 10.4; CGS reopening and redetermination job aid, December 2025"},
    {"match": r"^Medicare|^Railroad Medicare", "kinds": {"medicare"}, "days": 120, "clock": "remit", "verified": True,
     "label": "Medicare Part B: information or records the claim lacked go in a redetermination within 120 days of receipt of the remittance "
              "(42 CFR 405.942); a reopening takes no documents",
     "source": "42 CFR 405.942; CGS reopening and redetermination job aid, December 2025 (documentation is not reviewed in a reopening)"},
    {"match": r"^Humana", "kinds": {"medicare_advantage"}, "days": 365, "clock": "dos", "verified": True,
     "label": "Humana Medicare Advantage: corrected claim within 1 year. Humana's tip sheet counts the year from the original determination, "
              "but its manual limits claims to 1 year from the date of service, so the earlier date is shown",
     "source": "Humana, Claim Disputes and Corrected Claims tip sheet, August 2026; Humana Provider Manual 2026, claims submission"},
    {"match": r"^Humana", "kinds": {"medicaid"}, "days": 365, "clock": "dos", "verified": False,
     "label": "Humana Medicaid: corrected claim within 365 days of the date of service, the Kentucky plan's rule; the remittance does not say "
              "whether the member is in the Kentucky or the Indiana plan, and the Indiana plan's limit is not checked",
     "source": "Humana Healthy Horizons in Kentucky provider manual, 2025"},
    {"match": r"^Anthem", "kinds": {"medicare_advantage"}, "days": 365, "clock": "dos", "verified": True,
     "label": "Anthem Medicare Advantage: corrected claim within 365 days of the date of service, if the original claim was sent on time",
     "source": "Anthem Medicare Advantage provider guidebook (effective January 1, 2027); Anthem corrected claims reimbursement policy"},
]
AGREEMENT_FILING_NOTE = (" For a corrected claim the filing limit in the participation agreement applies instead, counted from the date of "
                         "service; it can be much shorter (often 90 days) and is not confirmed here.")

# What a Medicare Part B reopening can fix: a clerical error, such as a wrong or missing modifier or code. Information or
# records the claim lacked go in a redetermination, because a reopening takes no documents.
MEDICARE_CLERICAL_SIGNALS = {"EM_BUNDLED_NO_MODIFIER", "EM_BUNDLED_MOD57_MINOR", "DENIED_MISSING_MODIFIER", "DENIED_CODING"}
MEDICARE_CLERICAL_CARCS = {"4", "5", "6", "11", "146", "181", "182", "199"}         # a whole claim denied for its codes or modifiers


def corrected_rule(payer_group: str | None, payer_kind: str | None, rarcs: str | None = None, signal: str | None = None,
                   carc: str | None = None) -> dict | None:
    """The clock for fixing and resending a claim to this payer: {'days', 'clock' ('dos' or 'remit'), 'label', 'source',
    'verified'}, or None when only the participation agreement says. `rarcs` are the remarks on the line and its claim;
    `signal` and `carc` say whether a Medicare fix is clerical (a reopening) or needs records (a redetermination)."""
    grp, kind = payer_group or "", payer_kind or "other"
    clerical = signal in MEDICARE_CLERICAL_SIGNALS or (signal == "CLAIM_DENIED_UNPAID" and (carc or "") in MEDICARE_CLERICAL_CARCS)
    for r in CORRECTED_RULES:
        if r.get("rarc") and r["rarc"] not in (rarcs or ""):
            continue
        if r.get("clerical") and not clerical:
            continue
        if re.search(r["match"], grp) and (r["kinds"] is None or kind in r["kinds"]):
            return {k: r[k] for k in ("days", "clock", "label", "source", "verified")}
    return None


def appeal_rule(payer_group: str | None, payer_kind: str | None, plan: str | None = None) -> dict:
    """Filing window for the first-level dispute: {'days', 'label', 'source', 'verified'}."""
    grp, kind = payer_group or "", payer_kind or "other"
    if plan and "FEDERAL EMPLOYEE" in plan.upper():
        return {"days": 180, "verified": False, "source": "conservative default for the Federal Employee Program",
                "label": "Federal Employee Program: 180 days used as a conservative window; confirm the provider dispute period with the local Blue plan"}
    for r in APPEAL_RULES:
        if re.search(r["match"], grp) and (r["kinds"] is None or kind in r["kinds"]):
            return {"days": r["days"], "label": r["label"], "source": r["source"], "verified": r["verified"]}
    days, label = APPEAL_DEFAULTS.get(kind, APPEAL_DEFAULTS["other"])
    return {"days": days, "label": label, "source": "default by payer type", "verified": kind == "medicare"}


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
    "MEDICARE": "This is a request for redetermination under 42 CFR 405.940 et seq., submitted within 120 days of the initial determination.",
}

# Medicare same-day visit policy, CY 2027 physician fee schedule final rule (CMS-1848-F; CMS fact sheet November 4, 2026;
# Federal Register November 9, 2026): when a separately identifiable office visit is furnished on the same day as a 0-day
# global procedure, the most expensive service (procedure or visit) is paid at 100 percent and the other same-day
# procedures and visits at 80 percent. The proposed 50 percent and the 10- and 90-day globals were not finalized.
MEDICARE_SAME_DAY_EM = {"effective": "2027-01-01", "pct": 0.80, "confirmed": True}


def payer_group_kind(name: str | None, cfi: str | None) -> tuple[str, str]:
    """Payer group and kind for a claim: the payer name rules refined by the claim filing indicator (CLP06).
    Medicare Advantage and Medicaid products that share a commercial payer's name get their own group."""
    grp, kind = normalize_payer(name)
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
