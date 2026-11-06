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
    "M20": "Missing/incomplete/invalid HCPCS", "M25": "Information does not support the level of service billed; paid at lower level",
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

# Add-on codes: never subject to multiple procedure reduction (modifier 51 exempt)
ADD_ON_CODES = {"17003", "11103", "11105", "11107", "11201", "17312", "17314", "17315", "11001", "11008", "11046",
                "11047", "15003", "15005", "15101", "15111", "15116", "15121", "15131", "15136", "15151", "15152",
                "15156", "15157", "15201", "15221", "15241", "15261", "15271", "15272", "15273", "15274", "15275",
                "15276", "15277", "15278", "96367", "96368", "96375", "96376", "96570", "96571", "G2211", "G0463"}

# Global surgical period days for common dermatology procedures (Medicare)
GLOBAL_DAYS: dict[str, int] = {}
for _c in ["17311", "17312", "17313", "17314", "17315", "11102", "11103", "11104", "11105", "11106", "11107",
           "17000", "17003", "17004", "17110", "17111", "11900", "11901", "11055", "11056", "11057", "11720", "11721",
           "96910", "96912", "96913", "96920", "96921", "96922", "88305", "88304", "88307", "88312", "88341", "88342",
           "99024", "96372", "96401", "96402", "11981", "11982", "11983", "10021", "40490", "11755", "11760"]:
    GLOBAL_DAYS[_c] = 0
for _c in ["11200", "11201", "11300", "11301", "11302", "11303", "11305", "11306", "11307", "11308", "11310", "11311",
           "11312", "11313", "11400", "11401", "11402", "11403", "11404", "11406", "11420", "11421", "11422", "11423",
           "11424", "11426", "11440", "11441", "11442", "11443", "11444", "11446", "11600", "11601", "11602", "11603",
           "11604", "11606", "11620", "11621", "11622", "11623", "11624", "11626", "11640", "11641", "11642", "11643",
           "11644", "11646", "12001", "12002", "12004", "12005", "12006", "12007", "12011", "12013", "12014", "12015",
           "12016", "12017", "12018", "12031", "12032", "12034", "12035", "12036", "12037", "12041", "12042", "12044",
           "12045", "12046", "12047", "12051", "12052", "12053", "12054", "12055", "12056", "12057", "13100", "13101",
           "13102", "13120", "13121", "13122", "13131", "13132", "13133", "13150", "13151", "13152", "13153", "10060",
           "10061", "10080", "10081", "10120", "10121", "10140", "10160", "11042", "11043", "11044", "11450", "11451",
           "11462", "11463", "11470", "11471", "11740", "11750", "11765", "11770", "11771", "11772", "17106", "17107",
           "17108", "17250", "17260", "17261", "17262", "17263", "17264", "17266", "17270", "17271", "17272", "17273",
           "17274", "17276", "17280", "17281", "17282", "17283", "17284", "17286", "17340", "17360", "17380", "17999",
           "19000", "19001", "20550", "20551", "20600", "20605", "20610", "40800", "40808", "40810", "40812", "41100",
           "41105", "41108", "41110", "54050", "54055", "54056", "54057", "54060", "56501", "56515", "67810", "67840",
           "67850", "68020", "69100", "69105", "69110", "69120"]:
    GLOBAL_DAYS[_c] = 10
for _c in ["14000", "14001", "14020", "14021", "14040", "14041", "14060", "14061", "14301", "14302", "15100", "15110",
           "15115", "15120", "15130", "15135", "15150", "15155", "15200", "15220", "15240", "15260", "15570", "15572",
           "15574", "15576", "15730", "15731", "15733", "15734", "15736", "15738", "15740", "15750", "15760", "15770",
           "15780", "15781", "15782", "15783", "15786", "15787", "15788", "15789", "15792", "15793", "15819", "15820",
           "15821", "15822", "15823", "15824", "15825", "15826", "15828", "15829", "15830", "15832", "15833", "15834",
           "15835", "15836", "15837", "15838", "15839", "15840", "15841", "15842", "15845", "15847", "15860", "15876",
           "15877", "15878", "15879", "15920", "15922", "15931", "15933", "15934", "15935", "15936", "15937", "15940",
           "15941", "15944", "15945", "15946", "15950", "15951", "15952", "15953", "15956", "15958", "15999", "21011",
           "21012", "21013", "21014", "21015", "21016", "67900", "67901", "67902", "67903", "67904", "67906", "67908",
           "67909", "67911", "67912", "67914", "67915", "67916", "67917", "67921", "67922", "67923", "67924", "67930",
           "67935", "67938", "67950", "67961", "67966", "67971", "67973", "67974", "67975"]:
    GLOBAL_DAYS[_c] = 90

RADIATION_CODES = {"77401", "77402", "77407", "77412", "77427", "77431", "77432", "77435", "77436", "77437", "77280",
                   "77285", "77290", "77295", "77300", "77301", "77306", "77307", "77316", "77317", "77318", "77321",
                   "77331", "77332", "77333", "77334", "77336", "77370", "77385", "77386", "77387", "77600", "77605",
                   "77610", "77615", "77620", "G6001", "G6002", "G6003", "G6004", "G6005", "G6006", "G6007", "G6008",
                   "G6009", "G6010", "G6011", "G6012", "G6013", "G6014", "G6015", "G6016", "G6017", "0394T", "0395T"}

SKIN_SUBSTITUTE_RE = re.compile(r"^Q4[0-9]{3}$|^A2[0-9]{3}$|^C9[0-9]{3}$")
DRUG_RE = re.compile(r"^J[0-9]{4}$")


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


def carc_text(code: str | None) -> str:
    return CARC.get(code or "", "")


def rarc_text(code: str | None) -> str:
    return RARC.get(code or "", "")
