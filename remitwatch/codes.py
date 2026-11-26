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


def plan_state(plan: str | None = None, payer_name: str | None = None) -> str | None:
    """'KY' or 'IN' where the plan's name (the 835's contract class, which for Anthem's Medicaid plans starts with KENTUCKY,
    INDIANA or IN) or else the payer's name (a plan 'OF KENTUCKY') names the state; None otherwise (a plan naming another
    state is neither)."""
    for text in (plan, payer_name):
        u = " ".join(str(text or "").upper().split())
        if not u:
            continue
        if re.search(r"\bKENTUCKY\b", u) or u.startswith("KY "):
            return "KY"
        if re.search(r"\bINDIANA\b", u) or u.startswith("IN "):
            return "IN"
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
    pattern on the plan's name, 'state', 'carcs': the denial's reason is one of these, 'other_carcs': it is known and none
    of these, 'paid', 'cfi') needs its fact: a fact the item does not carry fails the condition."""
    if not re.search(r["match"], grp) or (r.get("kinds") is not None and kind not in r["kinds"]):
        return False
    if "plan" in r and not re.search(r["plan"], str(f.get("plan") or "").upper()):
        return False
    if "state" in r and f.get("state") != r["state"]:
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
    (a window that applies from an effective date, and the one before it), no_appeal, and the clock or the earliest date
    where the kind of window has one."""
    out = {k: r[k] for k in ("days", "label", "source", "verified")}
    out.update(months=r.get("months"), year_end=r.get("year_end"), receipt_days=int(r.get("receipt_days") or 0), legal=r.get("legal"),
               since=r.get("since"), before=_public(r["before"]) if r.get("before") else None, no_appeal=bool(r.get("no_appeal")))
    if "clock" in r:
        out["clock"] = r["clock"]
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
# a denial a plan's utilization management made (medical necessity, an authorization or referral): WellCare of Kentucky's
# Medicaid plan takes its appeal on a shorter clock than a claim-payment appeal of a coding, bundling or coverage denial
UM_CARCS = frozenset({"50", "55", "56", "167", "272", "B12", "B22", "15", "39", "62", "197", "198", "210", "243", "288"})
GEHA_MATCH = r"(?i)^(geha\b|government employees (health|hospital) association)"
FEP_PLAN = r"FEDERAL EMPLOYEE"
# a payer the name rules do not know (its group is its own name) whose claims carry Medicare's filing indicator (MA, MB):
# payer_group_kind calls it Medicare, but it is not Medicare, and Medicare's windows are not its own
NOT_MEDICARE_ITSELF = r"^(?!Medicare\b|Railroad Medicare\b)"

APPEAL_RULES: list[dict] = [
    {"match": r"", "plan": FEP_PLAN, "kinds": None, "days": 180, "verified": False, "legal": {"months": 6},
     "label": "Federal Employee Program: 180 days used as a safe date; a denial is reconsidered on the member's written request, made "
              "within '6 months from the date of the notice' that the claim was denied (5 CFR 890.105(b)(1)); confirm the provider "
              "dispute period with the local Blue plan",
     "source": "5 CFR 890.105(b)(1) (eCFR as of 2026-11-24); 2026 Service Benefit Plan brochure RI 71-005"},
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
    {"match": r"^WellCare", "kinds": {"medicaid"}, "state": "KY", "other_carcs": UM_CARCS, "days": 730, "months": 24, "verified": True,
     "legal": {"months": 24},
     "label": "WellCare of Kentucky Medicaid: 'Claim payment appeals must be submitted to WellCare in writing within 24 months of the date of "
              "denial of the EOP' (denials for untimely filing, incidental procedures, bundling, unlisted procedure codes and non-covered codes)",
     "source": "WellCare of Kentucky 2025-2026 Kentucky Medicaid Provider Manual, Claim Payment Appeals"},
    {"match": r"^WellCare", "kinds": {"medicaid"}, "state": "KY", "days": 60, "verified": True, "since": KRS_205_534_SINCE, "legal": KY_MCO_LEGAL,
     "label": "WellCare of Kentucky Medicaid: an appeal of a utilization-management or other denial within 60 calendar days of the denial "
              "(its 2025-2026 manual); " + KY_MCO_WORDS + ". A claim payment appeal of a coding, bundling or coverage denial has 24 months; "
              "where the denial's reason is not known the shorter window is used",
     "source": "WellCare of Kentucky 2025-2026 Kentucky Medicaid Provider Manual, Provider Appeals Process; " + KRS_SOURCE,
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
     "label": "Kentucky Medicaid (fee-for-service) takes no appeal of an ordinary claim denial: its dispute resolution, 'within thirty (30) "
              "calendar days of the date the notice was received', is only for the department's own determinations (an overpayment to "
              "repay, a sanction, payments withheld: 907 KAR 1:671 Sections 8 and 9). A denied claim is fixed by a corrected or new "
              "claim within 12 months of the date of service (907 KAR 1:673). A dispute is counted here from the remittance plus 5 days "
              "for receipt",
     "source": "907 KAR 1:671 Sections 8(1) and 9(4) (certified effective September 24, 2026); 907 KAR 1:673 Section 2(1)(b)"},
    {"match": r"^Aetna", "kinds": None, "days": 180, "verified": True,
     "label": "Aetna: 'File your reconsideration within 180 calendar days of the initial claim decision'; an appeal within 60 days of the "
              "reconsideration decision. For a fully insured plan Aetna allows 2 years in Kentucky (participating providers) and Indiana; "
              "the remittance does not say whether the plan is fully insured, so the shorter window is used",
     "source": "Aetna, Health care professionals: Disputes and appeals overview; Dispute & appeal process: state exceptions to filing "
               "standard (read 2026-11-26)"},
    {"match": r"^Cigna", "kinds": None, "days": 180, "verified": False,
     "label": "Cigna: payment review within 180 days of the initial payment or denial",
     "source": "Cigna reference guide (as reported in research)"},
    {"match": r"(?i)^champ\s*-?\s*va", "kinds": None, "days": 365, "months": 12, "verified": True, "legal": {"months": 12},
     "label": "CHAMPVA: a request for reconsideration 'within one year of the date of the initial determination' (38 CFR 17.277(b); one "
              "year also under 38 CFR 3.2500(a)(1)); a review by VA within 90 days of that decision",
     "source": "38 CFR 17.277(b) and 3.2500(a)(1) (eCFR as of 2026-11-24)"},
    {"match": GEHA_MATCH, "kinds": None, "days": 180, "verified": True, "legal": {"months": 6},
     "label": "GEHA (Federal Employees Health Benefits): 'Write to us within 6 months from the date of our decision' (5 CFR 890.105(b)(1)); "
              "the date shown is 180 days, the payer's own limit 6 months; then OPM's review within 90 days",
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
    {"match": r"^Humana", "kinds": {"medicare_advantage"}, "days": 365, "months": 12, "clock": "dos", "verified": True,
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
    {"match": r"^Anthem", "kinds": {"medicaid"}, "state": "KY", "days": 365, "clock": "dos", "verified": True,
     "label": "Anthem Kentucky Medicaid: a corrected claim 'when received within the applicable timely filing requirements of the original "
              "claim': 365 days from the date of service",
     "source": "Anthem Blue Cross and Blue Shield Medicaid (Kentucky) Reimbursement Policy G-16001, Corrected Claims (last approved 08/28/2023)"},
    {"match": r"", "plan": FEP_PLAN, "kinds": None, "days": 365, "year_end": 1, "clock": "dos", "verified": True,
     "label": "Federal Employee Program: a claim must reach the plan 'by December 31 of the following year after you received the service'; "
              "one returned for more information goes back 'within 90 days, or before the timely filing period expires, whichever is later'",
     "source": "2026 Blue Cross and Blue Shield Service Benefit Plan brochure RI 71-005, Section 7, Deadline for filing your claim"},
    {"match": r"^(UnitedHealthcare|UHC)(?! Integrated)", "kinds": None, "days": 90, "clock": "dos", "verified": True,
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
    {"match": r"^Medicaid KY$", "kinds": {"medicaid"}, "days": 365, "months": 12, "clock": "dos", "verified": True,
     "label": "Kentucky Medicaid (fee-for-service): a corrected or new claim, since 'Claims shall be submitted for payment within twelve (12) "
              "months of the date the service was rendered' (907 KAR 1:673); a claim billed to Medicare first may also go 'Within six (6) "
              "months of the Medicare adjudication date' if that is later (907 KAR 3:005)",
     "source": "907 KAR 1:673 Section 2(1)(b) (certified effective September 24, 2026); 907 KAR 3:005 Section 1 (amended effective July 16, 2026)"},
    {"match": r"^Medicaid IN$", "kinds": {"medicaid"}, "paid": False, "days": 180, "clock": "dos", "verified": True,
     "label": "Indiana Medicaid (fee-for-service): 'a denied claim that is resubmitted with corrected information is considered to be an "
              "initial claim and, as such, is subject to the 180-day timely filing limit', counted from the date of service",
     "source": "IHCP Provider Reference Module, Claim Submission and Processing (version 8.6, published November 24, 2026), Section 11"},
    {"match": r"^Medicaid IN$", "kinds": {"medicaid"}, "days": 60, "clock": "remit", "verified": True,
     "label": "Indiana Medicaid (fee-for-service): a paid claim's adjustment or replacement 'must be filed within 60 days of notification of "
              "the claim's disposition' (the date on the RA); a denied claim corrected is a new claim within 180 days of the service, and "
              "where the remittance does not say which, the shorter is used",
     "source": "IHCP Provider Reference Module, Claim Administrative Review and Appeals (March 24, 2026), Steps Taken Prior to the "
               "Administrative Review Process"},
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
            "no_appeal": False}


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
FILING_RULES: list[dict] = [
    {"match": r"^Medicare|^Railroad Medicare", "kinds": {"medicare"}, "days": 365, "months": 12, "verified": True,
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
    {"match": r"(?i)^champ\s*-?\s*va", "kinds": None, "days": 365, "months": 12, "verified": True,
     "label": "CHAMPVA: 'One year after the date of service' (38 CFR 17.276(a)(1))", "source": "38 CFR 17.276(a)(1)"},
    {"match": r"", "plan": FEP_PLAN, "kinds": None, "days": 365, "year_end": 1, "verified": True,
     "label": "Federal Employee Program: 'You must submit the claim by December 31 of the following year after you received the service'",
     "source": "2026 Blue Cross and Blue Shield Service Benefit Plan brochure RI 71-005, Section 7, Deadline for filing your claim"},
    {"match": GEHA_MATCH, "kinds": None, "days": 365, "year_end": 1, "verified": True,
     "label": "GEHA (Federal Employees Health Benefits): 'You must submit the claim by December 31 of the year after the year you received "
              "the service'",
     "source": "2026 GEHA Benefit Plan brochure RI 71-006, Section 7, Deadline for filing your claim"},
    {"match": r"^Ambetter", "kinds": None, "days": 90, "verified": True, "legal": {"days": 180},
     "label": "Ambetter: initial claims within 180 calendar days of the date of service for a participating provider and 90 for a "
              "non-participating one (its Kentucky and Indiana 2026 manuals); the date shown is 90 days, the participating limit 180",
     "source": "Ambetter from WellCare of Kentucky Provider and Billing Manual 2026; Ambetter (Indiana) Provider and Billing Manual 2026, Timely Filing"},
    {"match": r"^Anthem", "kinds": {"medicaid"}, "state": "KY", "days": 365, "verified": True,
     "label": "Anthem Kentucky Medicaid: claims 'must be received within 365 calendar days of the date of discharge for inpatient services "
              "and date of service for outpatient services'",
     "source": "Anthem Blue Cross and Blue Shield Kentucky Medicaid Provider Manual, 11.7 Timely Filing"},
    {"match": r"^Anthem", "kinds": {"medicaid"}, "state": "IN", "days": 90, "verified": True,
     "label": "Anthem Indiana Medicaid: 'Participating providers have 90 calendar days from the date of service' (nonparticipating ones too; "
              "as the second payer, 90 days from the other payer's RA)",
     "source": "Anthem Blue Cross and Blue Shield (Indiana Medicaid), IHCP Works 2026: Top denials and quick tips, October 2026"},
    {"match": r"^Anthem", "kinds": {"medicaid"}, "days": 90, "verified": True,
     "label": "Anthem Medicaid: the remittance does not say whether this is Anthem's Kentucky or its Indiana Medicaid plan, so the shorter "
              "limit is used: Indiana's 90 calendar days from the date of service (Kentucky's: 365 days)",
     "source": "Anthem (Indiana Medicaid), IHCP Works 2026: Top denials and quick tips; Anthem Kentucky Medicaid Provider Manual"},
    {"match": r"^WellCare", "kinds": {"medicaid"}, "state": "KY", "days": 365, "months": 12, "verified": True,
     "label": "WellCare of Kentucky Medicaid: 'Providers must submit claims (initialed, corrected and voided) within 12 months (or six months "
              "from the Medicaid or primary insurance payment date, whichever is later) from the date of service'; the six months after "
              "a primary payer's payment are not counted here",
     "source": "WellCare of Kentucky 2025-2026 Kentucky Medicaid Provider Manual, Timely Claims Submission"},
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
    for r in FILING_RULES:
        if _fits(r, grp, kind, f):
            return _public(r, earliest=r.get("earliest"))
    days, label = FILING_DEFAULTS.get(kind, FILING_DEFAULTS["other"])
    known = kind if kind in FILING_DEFAULTS else "other"
    return {"days": days, "label": label, "source": "common limit for this kind of payer", "verified": kind == "medicare",
            "earliest": FILING_EARLIEST.get(known), "months": FILING_DEFAULT_MONTHS.get(known), "year_end": None, "receipt_days": 0,
            "legal": None, "since": None, "before": None, "no_appeal": False}


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
