#!/usr/bin/env python3
"""Build remitwatch/payer_routes.json from the payer-routes research (unit `routes`, 2026-11-28).

    python3 -I plans/tools/routes/build_payer_routes.py <research_raw.json> remitwatch/payer_routes.json

The research file (the workflow's raw result: four clusters of payer x request rows, their sources, and an independent
verifier's verdicts) stays outside the repository; only the public payer facts the app needs are written out. The
verifier's corrections are applied here, each one listed in CORRECTIONS with its reason and evidence:

- a contradicted or outdated fact is fixed from the verifier's evidence, or dropped when the evidence gives no fix;
- an unverifiable fact is kept and marked unverified (the channel's `verified` is false, with why);
- facts the verifier found missing, with a source, are added where they change what a biller does.

Every phone and fax number is written 866-290-4036. Nothing here names the practice or a patient: the rows are the
payers' own published routes. Run it again after a new research round, then run tests/test_routes.py and
tests/test_docs.py (with REMITWATCH_PRIVATE_WORDS)."""
from __future__ import annotations

import json
import re
import sys
from urllib.parse import urlparse

CHECKED = "2026-11-28"
PREFIX = {"medicare": "m", "anthem_humana": "a", "uhc_optum": "u", "other_commercial_medicaid": "o"}
CHANNELS = ("payer_portal", "clearinghouse", "phone", "fax", "mail", "email", "not_accepted")
CHANNEL_OF = {"availity": "payer_portal", "clearinghouse_electronic": "clearinghouse"}
REQUESTS = ("medicare_reopening", "medicare_redetermination", "reconsideration_or_first_appeal", "second_level_appeal",
            "corrected_claim", "records_or_attachments", "overpayment_refund", "claim_status_or_reprocess", "timely_filing_proof")

# who published a source when the research left it out (the other-commercial cluster), by the source's host
PUBLISHER = {
    "www.aetna.com": "Aetna", "www.ambetterhealth.com": "Ambetter (Centene)", "www.in.gov": "Indiana Health Coverage Programs",
    "www.wellcare.com": "Wellcare (Centene)", "www.cigna.com": "Cigna Healthcare", "static.cigna.com": "Cigna Healthcare",
    "connections.cigna.com": "Cigna Healthcare", "www.umwafunds.org": "UMWA Health and Retirement Funds",
    "www.kymmis.com": "Kentucky Medicaid (Gainwell Technologies)", "www.wellcareky.com": "WellCare of Kentucky",
    "apps.legislature.ky.gov": "Kentucky General Assembly", "www.mhsindiana.com": "MHS Indiana (Centene)",
    "www.chfs.ky.gov": "Kentucky Cabinet for Health and Family Services", "www.cms.gov": "CMS",
    "www.cgsmedicare.com": "CGS Administrators, LLC", "www31.mutualofomaha.com": "Mutual of Omaha",
    "www.mutualofomaha.com": "Mutual of Omaha",
}

# Sources the corrections rest on (the verifier's evidence and its 'missing' facts), beside the research's own.
NEW_SOURCES = {
    "v1": {"url": "https://www.cgsmedicare.com/partb/forms/gateways/when_to.html", "title": "Part B Reopenings: when to use each form",
           "publisher": "CGS Administrators, LLC", "date": "read 2026-11-28"},
    "v2": {"url": "https://www.cgsmedicare.com/partb/overpay/offsets.html", "title": "Immediate Offsets",
           "publisher": "CGS Administrators, LLC", "date": "read 2026-11-28"},
    "v3": {"url": "https://www.cgsmedicare.com/partb/pubs/news/2026/11/news-214620.html",
           "title": "276 claim status inquiry edits (November 2026)", "publisher": "CGS Administrators, LLC", "date": "2026-11"},
    "v4": {"url": "https://www.wpsgha.com/guides-resources/view/timely-filing-of-claims", "title": "Timely Filing of Claims",
           "publisher": "WPS Government Health Administrators", "date": "content updated 2026-10-21"},
    "v5": {"url": "https://www.wpsgha.com/uploads/J8_B_Redetermination_of_an_Overpayment_Request_Form_1f05298b15.pdf",
           "title": "J8 Part B Redetermination of an Overpayment Request Form", "publisher": "WPS Government Health Administrators",
           "date": "2026-02-18"},
    "v6": {"url": "https://www.cms.gov/Regulations-and-Guidance/Guidance/Manuals/downloads/pim83c03.pdf",
           "title": "Medicare Program Integrity Manual, Chapter 3, section 3.2.3.2", "publisher": "CMS", "date": "Rev. 13821, 2026-06-09"},
    "v7": {"url": "https://www.c2cinc.com/Appellant-Signup", "title": "C2C appellant portal sign-up",
           "publisher": "C2C Innovative Solutions, Inc.", "date": "read 2026-11-28"},
    "v8": {"url": "https://www.c2cinc.com/Home/Submitting-FFS-Appeals-FAQs", "title": "Submitting FFS Appeals: FAQs",
           "publisher": "C2C Innovative Solutions, Inc.", "date": "read 2026-11-28"},
    "v9": {"url": "https://files.palmettogba.com/palmetto/rr.nsf/DID/29PQQRO3AE", "title": "Railroad Medicare redetermination forms",
           "publisher": "Palmetto GBA Railroad Medicare", "date": "read 2026-11-28"},
    "v10": {"url": "https://files.palmettogba.com/palmetto/rr.nsf/DID/GD9H2WEK6D", "title": "Railroad Medicare claim status by Sage and eChat",
            "publisher": "Palmetto GBA Railroad Medicare", "date": "2026-11-25"},
    "v11": {"url": "https://files.palmettogba.com/palmetto/rr.nsf/DID/AKAMSJ7021", "title": "Railroad Medicare immediate offset",
            "publisher": "Palmetto GBA Railroad Medicare", "date": "read 2026-11-28"},
    "v12": {"url": "https://www.anthem.com/content/dam/digital/docs/provider/commercial/general/in_contact_0002.pdf",
            "title": "Contact quick reference guide, Indiana", "publisher": "Anthem Blue Cross and Blue Shield", "date": "November 2025"},
    "v13": {"url": "https://www.anthem.com/content/dam/digital/docs/provider/commercial/general/KY-BCBS-Quick-Contact-Guide.pdf",
            "title": "Kentucky quick contact guide", "publisher": "Anthem Blue Cross and Blue Shield", "date": "read 2026-11-28"},
    "v14": {"url": "https://www.anthem.com/content/dam/digital/docs/provider/commercial/manuals/PM_KY_00011.pdf",
            "title": "Kentucky provider manual, Clinical Appeals", "publisher": "Anthem Blue Cross and Blue Shield", "date": "2026"},
    "v15": {"url": "https://www.anthem.com/content/dam/digital/docs/provider/commercial/manuals/PM_IN_00012.pdf",
            "title": "Indiana provider manual, Clinical Appeals", "publisher": "Anthem Blue Cross and Blue Shield", "date": "2026"},
    "v16": {"url": "https://assets.humana.com/is/content/humana/Medical%20Claim%20Appeals%20Tip%20Sheetpdf",
            "title": "Medical Claim Appeals Tip Sheet (831401ALL0925-B)", "publisher": "Humana", "date": "2025-09"},
    "v17": {"url": "https://assets.humana.com/is/content/humana/Claim%20Disputes%20and%20Corrected%20Claims%20Tip%20Sheetpdf",
            "title": "Claim Disputes and Corrected Claims Tip Sheet (NPC1055607ALL0826-B)", "publisher": "Humana", "date": "2026-08"},
    "v18": {"url": "https://www.humanamilitary.com/content/dam/sites/humana-military-com/provider/tricare-provider-handbook.pdf",
            "title": "TRICARE Provider Handbook, East Region", "publisher": "Humana Military", "date": "2026"},
    "v19": {"url": "https://www.uhcprovider.com/en/resource-library/edi/edi-transactions.html", "title": "EDI transactions",
            "publisher": "UnitedHealthcare", "date": "read 2026-11-28"},
    "v20": {"url": "https://www.uhcprovider.com/en/resource-library/provider-portal-resources/document-library.html",
            "title": "Document Library", "publisher": "UnitedHealthcare", "date": "read 2026-11-28"},
    "v21": {"url": "https://www.umr.com/print/UM2057.pdf", "title": "Post-service appeals in the provider portal (UM2057)",
            "publisher": "UMR (UnitedHealthcare)", "date": "2026-06"},
    "v22": {"url": "https://www.ecfr.gov/current/title-29/subtitle-B/chapter-XXV/subchapter-F/part-2560/section-2560.503-1",
            "title": "29 CFR 2560.503-1, claims procedure", "publisher": "eCFR", "date": "read 2026-11-28"},
    "v23": {"url": "https://vacommunitycare.com/api/vaccnportal/v1/webcomponentcode/trainingdoc/ccnClaims/CCNSTANDRECOUPR",
            "title": "Standard Recoupment Process", "publisher": "Optum (VA CCN)", "date": "2026-06-03"},
    "v24": {"url": "https://department.va.gov/vha/community-care/partnerships/community-care-network/",
            "title": "Community Care Network", "publisher": "U.S. Department of Veterans Affairs", "date": "updated 2026-07-06"},
    "v25": {"url": "https://www.aetna.com/content/dam/aetna/pdfs/aetnacom/healthcare-professionals/documents-forms/provider-complaint-appeal-request.pdf",
            "title": "Practitioner and Provider Complaint and Appeal Request (GR-69140)", "publisher": "Aetna", "date": "read 2026-11-28"},
    "v26": {"url": "https://www.aetna.com/content/dam/aetna/pdfs/aetnacom/data/forms_library/mcr-provider-complaint-appeal-request.pdf",
            "title": "Medicare Provider Complaint and Appeal Request (GR-69608)", "publisher": "Aetna", "date": "read 2026-11-28"},
    "v27": {"url": "https://www.aetna.com/content/dam/aetna/pdfs/aetnacom/healthcare-professionals/documents-forms/mncp-provider-complaint-appeal-request.pdf",
            "title": "Medicare Non Contracted Provider Complaint and Appeal Request (GR-69642)", "publisher": "Aetna", "date": "read 2026-11-28"},
    "v28": {"url": "https://www.aetna.com/health-care-professionals/disputes-appeals/disputes-appeals-overview.html",
            "title": "Disputes & Appeals Overview for Providers", "publisher": "Aetna", "date": "2026-11-05"},
    "v29": {"url": "https://static.cigna.com/assets/chcp/pdf/resourceLibrary/medical/RequestForHealthCareProfessionalPaymentReview.pdf",
            "title": "Request for Health Care Professional Payment Review", "publisher": "Cigna Healthcare", "date": "2021"},
    "v30": {"url": "https://www.cigna.com/health-care-providers/coverage-and-claims/appeals-disputes", "title": "Appeals and disputes",
            "publisher": "Cigna Healthcare", "date": "read 2026-11-28"},
    "v31": {"url": "https://www.cigna.com/health-care-providers/coverage-and-claims/submit-claims/electronic-data-interchange-vendors",
            "title": "Electronic data interchange vendors", "publisher": "Cigna Healthcare", "date": "read 2026-11-28"},
    "v32": {"url": "https://www.mhsindiana.com/content/dam/centene/mhsindiana/medicaid/pdfs/transportation-doc/0126.PR.P.JB_RR.9.13.a.V2.Provider_Manual_101325_Clean_508R.pdf",
            "title": "MHS Indiana provider manual", "publisher": "MHS Indiana (Centene)", "date": "2025-10-13"},
    "v33": {"url": "https://www.in.gov/medicaid/providers/files/modules/claim-adjustments.pdf", "title": "Claim Adjustments module",
            "publisher": "Indiana Health Coverage Programs", "date": "2026"},
    "v34": {"url": "https://www.in.gov/medicaid/providers/files/quick-reference.pdf", "title": "IHCP Quick Reference Guide v14.1",
            "publisher": "Indiana Health Coverage Programs", "date": "2026-05"},
    "v35": {"url": "https://www.healthspring.com/providers/news/1-1-2026-welcome-to-healthspring", "title": "Welcome to HealthSpring",
            "publisher": "HealthSpring", "date": "2026-01-01"},
    "v36": {"url": "https://healthspring.com/static/docs/providers/healthspring-manual-2026-participating-providers.pdf",
            "title": "HealthSpring provider manual 2026, participating providers", "publisher": "HealthSpring", "date": "2026"},
    "v37": {"url": "https://www.aetna.com/health-care-professionals/health-care-professional-forms.html",
            "title": "Health care professional forms", "publisher": "Aetna", "date": "read 2026-11-28"},
    "v38": {"url": "https://www.wellcareky.com/content/dam/centene/wellcare/ky/pdfs/provider/KY_Medicaid_Provider_Quick_Reference_Guide_2026_R.pdf",
            "title": "Kentucky Medicaid Provider Quick Reference Guide 2026", "publisher": "WellCare of Kentucky", "date": "2026"},
    "v39": {"url": "https://www.wellcare.com/-/media/pdfs/na/provider/resources/na_medicare_advantage_provider_manual_2026_r.ashx",
            "title": "Medicare Advantage Provider Manual 2026", "publisher": "Wellcare (Centene)", "date": "2026-04"},
    "v40": {"url": "https://www.ambetterhealth.com/content/dam/centene/kentucky/ambetter/pdf/2026-ky-provider-manual.pdf",
            "title": "Ambetter Kentucky provider manual 2026", "publisher": "Ambetter (Centene)", "date": "2026"},
    "v41": {"url": "https://www.ambetterhealth.com/content/dam/centene/mhsindiana/Ambetter/PDFs/2026-in-provider-manual.pdf",
            "title": "Ambetter Indiana provider manual 2026", "publisher": "Ambetter (Centene)", "date": "2026"},
    "v42": {"url": "https://www.kymmis.com/kymmis/pdf/billingInstr/PT64-65withMedicare_v9.9_(08-13-2026).pdf",
            "title": "Kentucky Medicaid billing instructions PT 64-65 with Medicare v9.9", "publisher": "Kentucky Medicaid (Gainwell Technologies)",
            "date": "2026-08-13"},
    "v43": {"url": "https://www.umwafunds.org/billing-claims/billing-address-contact-information/", "title": "Billing address and contact information",
            "publisher": "UMWA Health and Retirement Funds", "date": "updated 2026-06-24"},
    "v44": {"url": "https://www.cgsmedicare.com/jc/pubs/pdf/chpt7.pdf", "title": "Supplier manual chapter 7 (crossover claims)",
            "publisher": "CGS Administrators, LLC", "date": "read 2026-11-28"},
    "v45": {"url": "https://www.uhcprovider.com/content/dam/provider/docs/public/admin-guides/2026-UHC-Administrative-Guide.pdf",
            "title": "2026 UnitedHealthcare Care Provider Administrative Guide (UMR and UHSS supplement)", "publisher": "UnitedHealthcare",
            "date": "2026"},
    "v46": {"url": "https://www.uhcprovider.com/en/resource-library/news/2025/dc-ky-medicaid-documents-going-paperless.html",
            "title": "Kentucky Medicaid documents going paperless", "publisher": "UnitedHealthcare", "date": "2025-06-13"},
    "v47": {"url": "https://provider.humana.com/coverage-claims/payment-inquiries", "title": "Payment inquiries", "publisher": "Humana",
            "date": "read 2026-11-28"},
    "v48": {"url": "https://www.cgsmedicare.com/partb/cs/partb_ivr_user_guide.pdf", "title": "J15 Part B IVR User Guide",
            "publisher": "CGS Administrators, LLC", "date": "Revised May 18, 2026"},
}

# Groups: the research's payer groups, what each is, and the app's payer groups (codes.payer_group_kind) that use it.
# kinds: the payer kinds (codes) whose items it serves; states: the states its rows were checked for.
GROUPS = {
    "Medicare KY (CGS J15)": {"label": "CGS (Kentucky Medicare, J15)", "kinds": ["medicare"], "states": ["KY"]},
    "Medicare IN (WPS J8)": {"label": "WPS (Indiana Medicare, J8)", "kinds": ["medicare"], "states": ["IN"]},
    "Railroad Medicare": {"label": "Palmetto GBA (Railroad Medicare)", "kinds": ["medicare"], "states": ["KY", "IN"]},
    "Anthem / BCBS": {"label": "Anthem Blue Cross and Blue Shield", "kinds": ["commercial", "supplement", "other"], "states": ["KY", "IN"]},
    "Anthem / BCBS Medicare Advantage": {"label": "Anthem Medicare Advantage", "kinds": ["medicare_advantage"], "states": ["KY", "IN"]},
    "Humana": {"label": "Humana (commercial)", "kinds": ["commercial", "other"], "states": ["KY", "IN"]},
    "Humana Medicare Advantage": {"label": "Humana Medicare Advantage", "kinds": ["medicare_advantage"], "states": ["KY", "IN"]},
    "Humana Medicare Advantage Medicaid": {"label": "Humana Healthy Horizons in Kentucky (Medicaid)", "kinds": ["medicaid"], "states": ["KY"]},
    "Tricare": {"label": "TRICARE East (Humana Military)", "kinds": ["tricare", "commercial", "other"], "states": ["KY", "IN"]},
    "UnitedHealthcare": {"label": "UnitedHealthcare (commercial)", "kinds": ["commercial", "other"], "states": ["KY", "IN"]},
    "UnitedHealthcare Medicare Advantage": {"label": "UnitedHealthcare Medicare Advantage", "kinds": ["medicare_advantage"], "states": ["KY", "IN"]},
    "UMR": {"label": "UMR", "kinds": ["commercial", "other"], "states": ["KY", "IN"]},
    "United Healthcare Shared Services": {"label": "UnitedHealthcare Shared Services", "kinds": ["commercial", "other"], "states": ["KY", "IN"]},
    "UHC Integrated Services (Wausau)": {"label": "UnitedHealth Integrated Services (UMR's Wausau platform)", "kinds": ["commercial", "other"],
                                         "states": ["KY", "IN"]},
    "VA Community Care": {"label": "VA Community Care Network", "kinds": ["va", "commercial", "other"], "states": ["KY", "IN"]},
    "Aetna": {"label": "Aetna", "kinds": ["commercial", "medicare_advantage", "other"], "states": ["KY", "IN"]},
    "Cigna": {"label": "Cigna (commercial)", "kinds": ["commercial", "other"], "states": ["KY", "IN"]},
    "HealthSpring": {"label": "HealthSpring (Cigna Medicare Advantage since 2026)", "kinds": ["medicare_advantage", "supplement", "other"],
                     "states": ["KY", "IN"]},
    "WellCare / Ambetter (Centene) Medicaid": {"label": "WellCare of Kentucky Medicaid, or MHS Indiana Medicaid", "kinds": ["medicaid"],
                                               "states": ["KY", "IN"]},
    "WellCare / Ambetter (Centene) Medicare Advantage": {"label": "Wellcare Medicare Advantage", "kinds": ["medicare_advantage"],
                                                         "states": ["KY", "IN"]},
    "Ambetter (Centene)": {"label": "Ambetter (marketplace)", "kinds": ["commercial", "medicaid", "medicare_advantage", "other"],
                           "states": ["KY", "IN"]},
    "Medicaid KY": {"label": "Kentucky Medicaid fee-for-service (Gainwell)", "kinds": ["medicaid"], "states": ["KY"]},
    "Medicaid IN": {"label": "Indiana Medicaid fee-for-service (IHCP, Gainwell)", "kinds": ["medicaid"], "states": ["IN"]},
    "UMWA Health & Retirement Funds": {"label": "UMWA Health and Retirement Funds", "kinds": ["commercial", "medicare", "other"],
                                       "states": ["KY", "IN"]},
    "Medicare Supplement carriers": {"label": "A Medicare supplement (Medigap) carrier", "kinds": ["supplement", "commercial", "other"],
                                     "states": ["KY", "IN"]},
}

# Plans of a payer the research did not cover: the card says so in these words instead of guessing from the payer's other
# plans (the verifier's 'missing' facts). Matched by routes.py on the app's payer group.
NOT_COVERED = {
    "Anthem / BCBS Medicaid": ("Anthem's Medicaid plans (Kentucky; Indiana's HIP, Hoosier Healthwise, Hoosier Care Connect and "
                               "PathWays) were not covered. Anthem's Indiana guide lists Provider Disputes and Appeals, P.O. Box 61599, "
                               "Virginia Beach, VA 23466."),
    "Aetna Medicaid": ("Aetna Better Health of Kentucky (Medicaid) uses its own health plan's process, not Aetna's commercial and "
                       "Medicare routes (Aetna's forms page)."),
    "UnitedHealthcare Medicaid": ("UnitedHealthcare Community Plan of Kentucky (Medicaid) takes reconsiderations and appeals "
                                  "electronically only (since June 13, 2025) and has its own Kentucky manual; it was not covered."),
    "Humana Medicaid IN": ("Humana Healthy Horizons in Indiana (including Indiana PathWays for Aging) was not covered; Humana posts an "
                           "Indiana claim-payment inquiry guide on provider.humana.com."),
}

# The plan scope of each row that differs from its group's main one: (variant, when it applies, in words).
VARIANTS = {
    # Anthem commercial: BlueCard members of other Blue plans
    "a7": ("bluecard", "The member's card is another Blue plan's (BlueCard)"),
    "a8": ("bluecard", "The member's card is another Blue plan's (BlueCard)"),
    "a9": ("bluecard", "The member's card is another Blue plan's (BlueCard)"),
    "a10": ("bluecard", "The member's card is another Blue plan's (BlueCard)"),
    # Medicare Advantage plans: the practice is contracted with the large ones; a non-contracted route is an alternative
    "a13": ("nonpar", "Not contracted with the plan: a denial with member liability"),
    "a21": ("nonpar", "Not contracted with the plan"),
    "u14": ("nonpar", "Not contracted with the plan"),
    "o4": ("nonpar", "Medicare Advantage, not contracted with the plan"),
    "o32": ("nonpar", "Not contracted with the plan"),
    # Humana commercial: an appeal filed for the covered person
    "a29": ("member", "An appeal filed for the covered person, with their appointment of representative"),
    # Humana Healthy Horizons: a contracted-rate dispute
    "a36": ("rate", "Paid below the contracted rate"),
    # TRICARE East: the three first-level reviews
    "a45": ("coding_edit", "A line the claims auditing tool rejected (a correct coding edit)"),
    "a46": ("clinical", "Denied as not medically necessary or not covered (an appeal for the beneficiary)"),
    # VA Community Care: TriWest's regions 4 and 5
    "u47": ("triwest", "A Region 4 or 5 referral (CCN4 or CCN5, TriWest, payer ID TWVACCN)"),
    "u48": ("triwest", "A Region 4 or 5 referral (CCN4 or CCN5, TriWest, payer ID TWVACCN)"),
    "u49": ("triwest", "A Region 4 or 5 referral (CCN4 or CCN5, TriWest, payer ID TWVACCN)"),
    "u50": ("triwest", "A Region 4 or 5 referral (CCN4 or CCN5, TriWest, payer ID TWVACCN)"),
    "u51": ("triwest", "A Region 4 or 5 referral (CCN4 or CCN5, TriWest, payer ID TWVACCN)"),
    # Aetna: the second level differs for Medicare Advantage
    "o3": ("ma", "Medicare Advantage"),
    # WellCare of Kentucky Medicaid: clinical appeals; MHS Indiana Medicaid
    "o17": ("clinical", "Denied for no authorization, beyond the authorization, documentation, or late notice"),
    **{f"o{i}": ("mhs", "MHS (Managed Health Services), Indiana Medicaid") for i in range(23, 30)},
}
# rows a variant only reaches under this research group (the duplicates the research wrote under a second group name)
DUPLICATE_GROUPS = {"UHC Medicare Advantage": "UnitedHealthcare Medicare Advantage"}


def phone(text: str) -> str:
    """Every US phone or fax number in the text as 866-290-4036 (a leading 1 and any dots, spaces or brackets dropped)."""
    if not text:
        return text
    pat = re.compile(r"(?<![\w.-])(?:1[\s.-]?)?\(?(\d{3})\)?[\s.-]?(\d{3})[\s.-](\d{4})(?![\w-])")
    return pat.sub(lambda m: f"{m.group(1)}-{m.group(2)}-{m.group(3)}", text)


def url_of(where: str) -> str:
    """The web address a channel's 'where' gives, or ''."""
    w = (where or "").strip()
    if re.match(r"^https?://\S+$", w):
        return w
    if re.match(r"^(www\.)?[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+(/\S*)?$", w) and not re.search(r"@", w):
        return "https://" + w
    return ""


def deep(x):
    return json.loads(json.dumps(x))


# --------------------------------------------------------------------------------------------- the corrections
# Each entry: (row key, rank or None for the row, what to change, why, evidence). 'set' replaces fields; 'drop' takes the
# channel out; 'add' appends a channel; 'unverified' marks the channel (or the row's time limit) as not confirmed.
C2C_FIELDS = ["Sign up or sign in at the C2C appellant portal", "Select the appeal jurisdiction ({qic})",
              "Upload one PDF holding the reconsideration request form and all the documentation",
              "Optional: an Excel list of the claims (claim number, Medicare number, date of service, procedure code)"]
C2C_NOTES = ("C2C takes the request and its documentation as a single PDF (an optional Excel list of claims aside) and emails a "
             "confirmation when the upload succeeds.")
VA_REGION2 = ("Kentucky and Indiana are in CCN Region 2, which Optum administers (VA's Community Care Network page, updated July 6, "
              "2026); Optum's guideline tells staff to look for CCN1, CCN2 or CCN3 on the referral.")
EXCHANGE = ("Individual Exchange members (Anthem Essential Network, formerly Pathway Essentials) use 800-676-2583. Indiana numbers: "
            "Anthem's Indiana contact guide.")
WPS_FAX_CONFLICT = ("WPS documents disagree on the Indiana fax number: its J8 Part B Redetermination Request Form (02/19/26) prints "
                    "608-224-3502, and its overpayment redetermination form (02/18/2026) and How to Appeal guide print 608-224-3504. "
                    "Confirm the number with WPS before the first fax.")

CORRECTIONS: list[tuple] = [
    # ---------------------------------------------------------------- Medicare
    ("m0", 3, {"attachments": ["Have the claim's remittance advice at hand: the form asks for its details"],
               "notes_add": "CGS has a form for each correction type: BPC 346 (billed amount, procedure codes), DOS 568 (date of service), "
                            "RMF 780 (modifier), POS 419 (place of service), RNP 237 (rendering NPI), UBA 817 (units), REP 913 (reprocess "
                            "claim), GRF 679 (cancel a claim, MSP and other situations).", "sources_add": ["v1"]},
     "fixed", "The verifier could not confirm that the remittance advice goes with GRF 679: CGS's job aid says only to have it at hand. "
     "The type-specific forms were missing.", "https://www.cgsmedicare.com/partb/forms/job_aids/grf_679.html"),
    ("m0", 2, {"notes_add": "IVR hours for options 1 to 4 and 6: Monday to Friday 6 a.m. to 9 p.m. ET, Saturday 6 a.m. to 6 p.m. ET.",
               "sources_add": ["v48"]},
     "added", "The IVR hours were missing.", NEW_SOURCES["v48"]["url"]),
    ("m3", 2, {"notes_add": "IVR hours for options 1 to 4 and 6: Monday to Friday 6 a.m. to 9 p.m. ET, Saturday 6 a.m. to 6 p.m. ET.",
               "sources_add": ["v48"]}, "added", "The IVR hours were missing.", NEW_SOURCES["v48"]["url"]),
    ("m2", 1, {"fields": [f.format(qic="QIC Part B North") for f in C2C_FIELDS], "url": "https://www.c2cinc.com/Appellant-Signup",
               "notes_add": C2C_NOTES, "sources_add": ["v7", "v8"]},
     "fixed", "Contradicted: the C2C portal takes the request and every document as one PDF, not form fields.",
     "https://www.c2cinc.com/portals/0/Documents/C2C-Portal-User-Guide-508.pdf"),
    ("m10", 1, {"fields": [f.format(qic="QIC Part B North") for f in C2C_FIELDS], "url": "https://www.c2cinc.com/Appellant-Signup",
                "notes_add": C2C_NOTES, "sources_add": ["v7", "v8"]},
     "fixed", "The same C2C portal rows as CGS's: one PDF, not form fields.",
     "https://www.c2cinc.com/portals/0/Documents/C2C-Portal-User-Guide-508.pdf"),
    ("m18", 1, {"fields": [f.format(qic="QIC Part B South") for f in C2C_FIELDS], "url": "https://www.c2cinc.com/Appellant-Signup",
                "notes_add": C2C_NOTES, "sources_add": ["v7", "v8"]},
     "fixed", "The same C2C portal rows as CGS's: one PDF, not form fields.",
     "https://www.c2cinc.com/portals/0/Documents/C2C-Portal-User-Guide-508.pdf"),
    ("m4", 1, {"notes": "Put the ADR letter on top of each claim's records, whichever route you use. Do not send the whole chart."},
     "fixed", "Unverifiable: CGS does not say to use one route only; it says to put the ADR letter on top whichever route is used.",
     "https://www.cgsmedicare.com/partb/pubs/news/2018/01/cope6009.html"),
    ("m6", 4, {"fields_replace": {"Date of service or ICN": "Date of service (MMDDYY)"}},
     "fixed", "Contradicted: the IVR requires and validates the date of service (MMDDYY) before it gives a claim's status.",
     "https://www.cgsmedicare.com/partb/cs/partb_ivr_user_guide.pdf"),
    ("m6", 1, {"notes_add": "From November 2026 CGS rejects a 276 whose NPI (2100C NM109) is not associated with the submitter "
                            "(2100B NM109) or whose 2200D REF02 has fewer than two non-space characters.", "sources_add": ["v3"]},
     "added", "A November 2026 CGS edit on 276 inquiries was missing.", NEW_SOURCES["v3"]["url"]),
    ("m5", 1, {"notes_add": "To pay a demanded debt by offset: myCGS eOffset (Financial Tools > Financial Forms), or the Immediate Offset "
                            "Request Form faxed to 615-664-5916.", "sources_add": ["v2"]},
     "added", "The immediate offset route was missing.", NEW_SOURCES["v2"]["url"]),
    ("m8", 2, {"unverified": WPS_FAX_CONFLICT + " WPS's own pages describe faxing redeterminations with the cover sheet; that a clerical "
                             "reopening goes the same way was not confirmed."},
     "unverified", "Unverifiable: the Indiana fax number differs between WPS documents, and the fax route for a clerical reopening "
     "was not confirmed.", "https://www.wpsgha.com/guides-resources/view/how-to-appeal-a-claim-determination"),
    ("m11", 2, {"unverified": WPS_FAX_CONFLICT}, "unverified", "The same fax number conflict as the WPS reopening row.",
     "https://www.wpsgha.com/uploads/J8_B_Redetermination_Request_Form_c69dfa1183.pdf"),
    ("m9", 2, {"unverified": WPS_FAX_CONFLICT}, "unverified", "The same fax number conflict.",
     "https://www.wpsgha.com/uploads/J8_B_Redetermination_Request_Form_c69dfa1183.pdf"),
    ("m12", None, {"time_limit": "45 calendar days from the ADR letter for a request from the MAC, a RAC or the SMRC; 30 for a UPIC "
                                 "request (CMS Program Integrity Manual, chapter 3, section 3.2.3.2). The letter gives its own due date.",
                   "sources_add": ["v6"]},
     "fixed", "Unverifiable in WPS's sources; CMS's manual sets the day counts for every MAC.", NEW_SOURCES["v6"]["url"]),
    ("m13", 1, {"notes_add": "To appeal an overpayment demand: SNAP Request Redetermination with 'Appealing an overpayment? Yes' (it "
                             "pauses recoupment), or the J8 Part B Redetermination of an Overpayment Request Form, fax 608-224-3504 or "
                             "mail WPS Government Services ATTN: Overpayment Appeals PO Box 8580 Madison, WI 53708-8580.",
                "sources_add": ["v5"]},
     "added", "The overpayment appeal route was missing.", NEW_SOURCES["v5"]["url"]),
    ("m15", 1, {"where": "WPS Government Services Part B Indiana Claims Manager PO Box 8940 Madison, WI 53708-8940", "url": "",
                "attachments": ["Original paper (red and white) CMS-1500 claim form",
                                "Proof of good cause, for example the beneficiary's retroactive Medicare entitlement letter from the "
                                "Social Security Administration", "Request for Waiver to Extend the Timely Filing Limit form"],
                "notes": "WPS's Timely Filing of Claims page (content updated October 21, 2026).", "sources_add": ["v4"]},
     "fixed", "Contradicted: WPS's examples of proof are a retroactive entitlement letter, not a Medicaid recoupment letter; the "
     "Claims Manager's address was found.", NEW_SOURCES["v4"]["url"]),
    ("m15", None, {"confidence": "medium"}, "fixed", "The route now rests on WPS's own page.", NEW_SOURCES["v4"]["url"]),
    ("m16", 2, {"fields": ["Forms tab > Select a Topic > Appeals > Reopening: Simple Claim Correction (provider data pre-filled)",
                           "Every field marked with a red asterisk", "PDF attachments (40 MB each, 150 MB total)"]},
     "fixed", "Unverifiable: Palmetto says to fill every field marked with a red asterisk; the list of those fields was not confirmed.",
     "https://files.palmettogba.com/palmetto/rr.nsf/DID/TC87H5CHM2"),
    ("m17", None, {"time_limit": "120 days from receipt of the initial determination (presumed received 5 days after the notice "
                                 "date). After 120 days, use the same Redetermination: 1st Level Appeal form (AP-RRB-B-1000, now for "
                                 "timely and late requests) or the eServices 'Redetermination: 1st Level Appeal, Late Submission' eForm, "
                                 "and give the reason.", "sources_add": ["v9"]},
     "fixed", "Outdated: the consolidated Redetermination form now serves late requests too.", NEW_SOURCES["v9"]["url"]),
    ("m21", 2, {"notes_add": "To appeal an overpayment: the eServices 'Redetermination: Overpayment Appeal' eForm, or the paper "
                             "AP-RRB-B-1000 with 'Overpayment (OP) Appeal' checked, the demand letter and the overpayment spreadsheet, "
                             "fax 803-462-2218. Immediate offset by mail: the Immediate Offset Request form to Palmetto GBA Railroad "
                             "Medicare PO Box 10066 Augusta, GA 30999.", "sources_add": ["v9", "v11"]},
     "added", "The overpayment appeal and the immediate offset by mail were missing.", NEW_SOURCES["v9"]["url"]),
    ("m22", 2, {"notes_add": "Status also from Sage, the website's digital assistant (Monday to Friday 8 a.m. to 9 p.m. ET), and secure "
                             "eChat (Monday to Friday 9:30 a.m. to 2 p.m. ET).", "sources_add": ["v10"]},
     "added", "Sage and eChat were missing.", NEW_SOURCES["v10"]["url"]),
    # ---------------------------------------------------------------- Anthem and Humana
    ("a0", 4, {"notes": "Kentucky quick contact guide, local products. " + EXCHANGE + " The manual says to call the number on the back "
                        "of the member ID card.", "sources_add": ["v12", "v13"]},
     "fixed", "Contradicted: 800-345-4344 does not cover individual Exchange members; an Indiana contact guide exists.",
     NEW_SOURCES["v13"]["url"]),
    ("a1", 2, {"notes_add": EXCHANGE, "sources_add": ["v12", "v13"]}, "fixed", "The same Exchange number correction.",
     NEW_SOURCES["v13"]["url"]),
    ("a4", 1, {"drop": True}, "dropped", "Contradicted: an 837 with PWK carries records sent with the first claim without being asked "
     "(unsolicited); records Anthem asked for go through Claim Status, Send Attachment.",
     "https://www.anthem.com/content/dam/digital/docs/provider/commercial/manuals/PM_IN_00012.pdf"),
    ("a4", 2, {"notes_add": "Records sent with a first claim without being asked can go with the 837 (PWK) or Direct Data Entry."},
     "fixed", "The PWK route is for unsolicited records.", "https://www.anthem.com/content/dam/digital/docs/provider/commercial/manuals/PM_IN_00012.pdf"),
    ("a5", "add", {"channel": "phone", "name": "Recovery", "where": "800-345-7029", "minutes": 12, "sources": ["v12", "v13"],
                   "notes": "Both contact guides list Recovery for local products."},
     "added", "The overpayment phone line was missing.", NEW_SOURCES["v13"]["url"]),
    ("a15", 2, {"unverified": "Anthem's MA manual names the online claim form; that it takes MA corrected claims (Billing Frequency, "
                              "Payer Claim Control Number) was not confirmed."},
     "unverified", "Unverifiable for Medicare Advantage corrected claims.",
     "https://www.anthem.com/content/dam/digital/docs/provider/commercial/manuals/ABSCRPM-0010-20.pdf"),
    ("a21", "add", {"channel": "fax", "name": "Humana appeals fax", "where": "800-949-2961", "minutes": 12, "sources": ["v16"],
                    "notes": "From the Medical Claim Appeals Tip Sheet (831401ALL0925-B) with P.O. Box 14165 Lexington, KY 40512-4165; "
                             "Humana's 2025 inquiry guide labels it the Medicare expedited fax. A nonparticipating provider may use the "
                             "Humana Appeal, Complaint or Grievance Form (skip Section 4) with a Waiver of Liability."},
     "fixed", "Contradicted: Humana's tip sheet lists a fax for these appeals.", NEW_SOURCES["v16"]["url"]),
    ("a23", None, {"time_limit": "Unless a contract states differently, a corrected claim must be submitted within 1 year from the "
                                 "original claim determination (Humana's Claim Disputes and Corrected Claims Tip Sheet, August 2026).",
                   "sources_add": ["v17"]},
     "fixed", "Contradicted: Humana's tip sheet sets 1 year from the original claim determination.", NEW_SOURCES["v17"]["url"]),
    ("a23", 1, {"notes": "Humana's Claim Disputes and Corrected Claims Tip Sheet: 'Populate loop 2300 in CLM05-3 with claim frequency "
                         "type code 7 to replace or correct prior claim'.", "sources_add": ["v17"]},
     "fixed", "Contradicted: a Humana page for Medicare Advantage does state the EDI format.", NEW_SOURCES["v17"]["url"]),
    ("a23", 3, {"where": "The claims address on the back of the member's ID card", "sources_add": ["v17"],
                "notes": "Humana's tip sheet sends paper corrected claims to the address on the ID card. Humana charges $5 per paper "
                         "claim when the reimbursement is $10 or more."},
     "fixed", "The tip sheet names the ID card's address for paper corrected claims.", NEW_SOURCES["v17"]["url"]),
    ("a39", 2, {"unverified": "From Humana's general Availity flyer; that Correct this Claim works for Kentucky Medicaid was not confirmed."},
     "unverified", "Unverifiable for Kentucky Medicaid.", "https://assets.humana.com/is/content/humana/FINAL_653104ALL0125_ClaimToolsFlyer_2025pdf"),
    ("a45", None, {"time_limit": "Within 90 calendar days of the remittance, as an allowable charge review: the 2026 handbook lists "
                                 "'Claims auditing tool denials (except assistant surgeons)' among them. Humana Military answers within "
                                 "30 days of receipt.", "sources_add": ["v18"]},
     "fixed", "Contradicted: claims auditing reconsiderations have the allowable charge review's 90 days.", NEW_SOURCES["v18"]["url"]),
    # ---------------------------------------------------------------- UnitedHealthcare, UMR, VA
    ("u4", 2, {"drop": True}, "dropped", "Contradicted: UHC takes an EDI 275 only as an unsolicited attachment within 5 days of the "
     "original claim; requested records go through the portal or mail.", NEW_SOURCES["v19"]["url"]),
    ("u4", 1, {"notes_add": "An EDI 275 goes only as an unsolicited attachment within 5 days of the original claim; after that, use the "
                            "portal or mail. UHC's letters, record requests included, arrive in the portal's Document Library, not by mail.",
               "sources_add": ["v19", "v20"]},
     "fixed", "The 275 limit and the Document Library were missing.", NEW_SOURCES["v20"]["url"]),
    ("u11", 2, {"drop": True}, "dropped", "The same 275 correction as the commercial row.", NEW_SOURCES["v19"]["url"]),
    ("u11", 1, {"notes_add": "An EDI 275 goes only as an unsolicited attachment within 5 days of the original claim; after that, use the "
                             "portal or mail. UHC's letters arrive in the portal's Document Library.", "sources_add": ["v19", "v20"]},
     "fixed", "The 275 limit and the Document Library were missing.", NEW_SOURCES["v20"]["url"]),
    ("u23", 1, {"fields": ["Sign in at umr.com (every TIN registered)", "Patient search: the member ID (or Social Security number) and "
                           "the date of birth", "Select the patient", "Show me: Claims/Claims appeals"], "sources_add": ["v21"]},
     "fixed", "Outdated: UMR's June 2026 guide (UM2057) asks for the member ID or SSN with the date of birth.", NEW_SOURCES["v21"]["url"]),
    ("u24", 1, {"name": "umr.com claim inquiry (Appeal/review this claim > Request claim inquiry)",
                "notes": "Only the member can appeal: a provider files a claim inquiry (UMR answers within 120 days), or the member "
                         "approves an appeal in the member portal (30 to 60 days). Status: Claims > Claim appeal status; the "
                         "confirmation page gives a transaction number (UMR guide UM2057, June 2026).", "sources_add": ["v21"]},
     "fixed", "Outdated: since UM2057 (June 2026) providers file a claim inquiry; only the member can appeal.", NEW_SOURCES["v21"]["url"]),
    ("u24", None, {"time_limit": "UMR publishes no provider limit; the self-funded plan document and your contract set it (a member's "
                                 "ERISA appeal has at least 180 days from the denial notice).", "time_limit_unverified": True,
                   "sources_add": ["v22"]},
     "unverified", "Unverifiable: no UMR provider source states a limit.", NEW_SOURCES["v22"]["url"]),
    ("u25", 1, {"unverified": "UMR's own pages print only the paper rule; the electronic one (CLM05-3 = 7, REF02) comes from the main "
                              "UnitedHealthcare guide."},
     "unverified", "Unverifiable for UMR.", NEW_SOURCES["v45"]["url"]),
    ("u27", 1, {"unverified": "The form's field list was not confirmed on UMR's form center."},
     "unverified", "Unverifiable: the form's fields.", "https://www.umr.com/form-center"),
    ("u35", 1, {"sources_remove": [48]}, "fixed", "Contradicted: that source shows UMR's pre-determination address, not its claims "
     "offices.", NEW_SOURCES["v45"]["url"]),
    ("u41", 3, {"unverified": "The fax inquiry's field list was not confirmed."}, "unverified", "Unverifiable: the fields.",
     "https://vacommunitycare.com/api/vaccnportal/v1/webcomponentcode/trainingdoc/ccnClaims/ccnCProcessing"),
    ("u45", None, {"time_limit": "Repay within 30 calendar days of the recoupment letter; if nothing arrives within 61 days, Optum offsets "
                                 "later payments. A dispute goes in writing with the letter; a decision is expected within 30 business "
                                 "days.", "confidence": "medium", "sources_add": ["v23"]},
     "fixed", "Contradicted: Optum's Standard Recoupment Process states the limits and a dispute address.", NEW_SOURCES["v23"]["url"]),
    ("u45", "add", {"channel": "mail", "name": "Dispute of a recoupment letter, in writing", "minutes": 15, "sources": ["v23"],
                    "where": "VA CCN Correspondence P.O. Box 202118 Florence, SC 29501", "attachments": ["The recoupment letter"],
                    "notes": "No medical records with a dispute."},
     "fixed", "The dispute route was missing.", NEW_SOURCES["v23"]["url"]),
    ("u48", 1, {"unverified": "The online form's field list was not confirmed."}, "unverified", "Unverifiable: the fields.",
     "https://claimsreconsideration.triwest.com/"),
    # ---------------------------------------------------------------- Aetna, Cigna, Centene, Medicaid
    ("o2", 2, {"fields": ["Today's Date", "Member's ID Number", "Plan Type", "Member's Group Number (Optional)", "Claim ID Number(s)",
                          "Reference Number/Authorization Number", "Service Date(s)", "Initial Denial Notification Date(s)",
                          "Reconsideration Denial Notification Date(s)", "CPT/HCPC/service disputed", "Provider name, TIN/NPI, group",
                          "Contact name, title, address, phone, fax, email", "Explanation of your request"], "sources_add": ["v25"]},
     "fixed", "Contradicted: the form's order is date, member ID, plan type, group number, then the claim, reference, service and "
     "denial dates.", NEW_SOURCES["v25"]["url"]),
    ("o3", 2, {"fields": ["Today's Date", "Member's ID Number", "Plan Type", "Member's Group Number (Optional)", "Claim ID Number(s)",
                          "Reference Number/Authorization Number", "Service Date(s)", "Initial Denial Notification Date(s)",
                          "Reconsideration Denial Notification Date(s)", "CPT/HCPC/service disputed", "Provider name, TIN/NPI, group",
                          "Contact name, title, address, phone, fax, email", "Explanation of your request"], "sources_add": ["v26"]},
     "fixed", "Contradicted: the same order on GR-69608.", NEW_SOURCES["v26"]["url"]),
    ("o4", 1, {"fields": ["Today's Date", "Member's ID Number", "Plan Type", "Member's Group Number (Optional)", "Claim ID Number(s)",
                          "Reference Number/Authorization Number", "Service Date(s)", "CPT/HCPC/service disputed",
                          "Provider name, TIN/NPI, group", "Contact name, title, address, phone, fax, email",
                          "Explanation of your request", "Waiver: enrollee name and ID, provider, dates of service, health plan, "
                          "signature, date"], "sources_add": ["v27"]},
     "fixed", "Contradicted: GR-69642 has no denial dates and its own order.", NEW_SOURCES["v27"]["url"]),
    ("o0", "add", {"channel": "phone", "name": "Status of a dispute or appeal sent by mail or fax", "minutes": 10, "sources": ["v28"],
                   "where": "Non-Medicare 888-632-3862; Medicare 800-624-0756"},
     "added", "The dispute status lines were missing.", NEW_SOURCES["v28"]["url"]),
    ("o7", 1, {"unverified": "The InstaMed form's field list was not confirmed."}, "unverified", "Unverifiable: the fields.",
     "https://pay.instamed.com/Form/PaymentPortal/Default?id=aetnaoverpayment"),
    ("o9", 3, {"fields": ["Have services been rendered? (yes or no)", "Employer Name", "Account Number (from the Cigna ID card)",
                          "Patient Name", "Date of Birth", "State of Residence", "Date(s) of Service", "Procedure/Type of Service",
                          "Referring Health Care Professional Name (if applicable)"],
               "notes": "If the ID card shows 'GWH-Cigna' or 'G': Cigna Healthcare Inc. National Appeals Unit (NAO) PO Box 188062 "
                        "Chattanooga, TN 37422.", "sources_add": ["v29", "v30"]},
     "fixed", "Contradicted: the form's fields are the ones it prints; outdated: the GWH-Cigna address.", NEW_SOURCES["v29"]["url"]),
    ("o12", 1, {"notes": "Only some EDI vendors send Cigna 275 attachments: Cigna's vendor list (read November 28, 2026) shows SSI Group "
                         "for medical attachments, and TriZetto Provider Solutions (Gateway EDI) for eligibility only. Check yours first.",
                "rank": 2.5, "sources_add": ["v31"]},
     "fixed", "Contradicted: not every EDI vendor sends 275s to Cigna.", NEW_SOURCES["v31"]["url"]),
    ("o14", 1, {"unverified": "Cigna does not say that a reconsideration on CignaforHCP takes timely filing proof."},
     "unverified", "Unverifiable.", "https://static.cigna.com/assets/chcp/pdf/resourceLibrary/eCourses/claim-reconsideration-requests-online-step-by-step-guide.pdf"),
    ("o14", 3, {"name": "Payment review form, issue 'Other', with the proof attached",
                "notes": "The form's 'Timely claim filing (without proof)' box is for a request without proof: with proof, check "
                         "'Other' and say so.", "sources_add": ["v29"]},
     "fixed", "Contradicted: the 'without proof' issue does not fit a request that carries proof.", NEW_SOURCES["v29"]["url"]),
    ("o29", 1, {"channel": "payer_portal", "name": "Reconsideration on the MHS secure portal, with the proof", "where": "mhsindiana.com",
                "url": "https://mhsindiana.com",
                "attachments": ["For an EDI rejection: the payer rejection report from your clearinghouse"],
                "notes": "MHS: 'EDI rejections require the provider to contact their clearinghouse and obtain a payer rejection "
                         "report'; for retroactive eligibility, file a portal reconsideration with the eligibility details.",
                "unverified": "MHS names the report as proof; that the portal takes the reconsideration with it was not confirmed.",
                "sources_add": ["v32"]},
     "fixed", "Contradicted: the clearinghouse's rejection report is the proof, not a route to send it.", NEW_SOURCES["v32"]["url"]),
    ("o53", 3, {"attachments_add": ["IHCP Third-Party Liability (TPL)/Medicare Special Attachment Form (required for every adjustment "
                                    "to the details of a professional claim, crossovers included)"], "sources_add": ["v33"]},
     "fixed", "Contradicted: the TPL/Medicare Special Attachment Form is required for these adjustments.", NEW_SOURCES["v33"]["url"]),
    ("o56", 2, {"name": "Paper claim with its own set of proof", "where": "Gainwell – CMS-1500 Claims PO Box 50447 Indianapolis, IN 46250-0418",
                "notes": "PO Box 50440 is for paper attachments to electronic claims. Not accepted as proof: your own notes or "
                         "timelines.", "sources_add": ["v34"]},
     "fixed", "Contradicted: a professional paper claim goes to the CMS-1500 Claims box.", NEW_SOURCES["v34"]["url"]),
    ("o46", None, {"time_limit_unverified": True}, "unverified", "Unverifiable: 'no separate limit for adjusting a paid claim'.",
     "https://www.kymmis.com/kymmis/pdf/billingInstr/PT64-65withMedicare_v9.9_(08-13-2026).pdf"),
    ("o16", 1, {"notes_add": "Payment policy disputes go by the code on the EOP (24 months from the denial): IH, CE, MK, CV (records "
                             "required), PD, DN250 or ERLOC to WellCare of Kentucky Attn: Payment Policy Disputes Department P.O. Box "
                             "31426 Tampa, FL 33631-3426; LT or RVLT to Attn: CCR P.O. Box 31394 Tampa, FL 33631-3394; RVPI and DN173 "
                             "to P.O. Box 31416 Tampa, FL 33631-3416; CPI to Optum P.O. Box 52846 Philadelphia, PA 19115 (fax "
                             "267-687-0994).", "sources_add": ["v38"]},
     "added", "The payment policy dispute addresses by EOP code were missing.", NEW_SOURCES["v38"]["url"]),
    ("o21", 1, {"notes_add": "Single-claim checks: the Refund Check Informational Sheet; more than 25 claims: the Refund Referral Grid, "
                             "emailed to OverpaymentRefunds@wellcare.com.", "sources_add": ["v38"]},
     "added", "The refund forms were missing.", NEW_SOURCES["v38"]["url"]),
    ("o35", 2, {"notes_add": "Single-claim checks: the Refund Check Informational Sheet; more than 25 claims: the Refund Referral Grid, "
                             "mailed to Wellcare Attn: CCU Recovery P.O. Box 31658 Tampa, FL 33631-3658.", "sources_add": ["v39"]},
     "added", "The refund forms were missing.", NEW_SOURCES["v39"]["url"]),
    ("o31", 1, {"fields": ["Date(s) of service", "Place of service code", "Denial reason code", "Member name",
                           "Member ID and/or date of birth", "Claim number", "Provider name, address, telephone, fax and contact person",
                           "Provider Tax ID", "Total billed charges", "Authorization number", "Reason statement",
                           "Supporting documents"],
                "notes_add": "Disputes without these are upheld. A written reopening request (stating the specific reason) is a "
                             "separate route for clerical errors.", "sources_add": ["v39"]},
     "added", "The dispute's field list was missing.", NEW_SOURCES["v39"]["url"]),
    ("o49", 1, {"notes_add": "When an MCO member is made retroactively fee-for-service: attach the MCO's paid remittance and its void "
                             "remittance behind the claim; the claim must be received and processed within one year of the MCO void "
                             "EOB date.", "sources_add": ["v42"]},
     "added", "The retroactive fee-for-service proof was missing.", NEW_SOURCES["v42"]["url"]),
    ("o57", "add", {"channel": "email", "name": "Provider Services, Medical Claims (claim questions, not appeals)",
                    "where": "fundsclaims@optum.com", "minutes": 10, "sources": ["v43"]},
     "added", "The claims email was missing.", NEW_SOURCES["v43"]["url"]),
    ("o62", 2, {"notes_add": "For a physician's claim the Part B contractor's rules apply (CGS for Kentucky, WPS for Indiana).",
                "sources_add": ["v44"]}, "added", "The supplement row cited a supplier manual.", NEW_SOURCES["v44"]["url"]),
]

# Rows the verifier's 'missing' facts add (each with its sources).
NEW_ROWS = [
    {"key": "n1", "group": "Anthem / BCBS", "request": "reconsideration_or_first_appeal", "variant": "clinical",
     "when": "Denied as not medically necessary or experimental (a clinical decision)",
     "scope": "Commercial and individual plans, Kentucky and Indiana: clinical appeal of a medical necessity denial",
     "time_limit": "180 calendar days from the notice of the initial decision.", "confidence": "medium",
     "channels": [
         {"channel": "payer_portal", "name": "Availity Authorizations & Referrals (a denied prior authorization)", "where": "https://Availity.com",
          "minutes": 10, "sources": ["v14", "v15"]},
         {"channel": "phone", "name": "Provider Services (a clinical appeal can be made verbally)", "where": "800-345-4344", "minutes": 15,
          "sources": ["v14", "v15"]},
         {"channel": "mail", "name": "Grievances and Appeals", "minutes": 20, "sources": ["v14", "v15"],
          "where": "Anthem Blue Cross and Blue Shield Attention: Grievances and Appeals P.O. Box 105568 Atlanta, GA 30348-5568",
          "fields": ["Provider name, address, phone, email, NPI or TIN", "Member name and Anthem ID",
                     "Claim, authorization or reference number and date(s) of service", "Specific reasons the decision is wrong"],
          "attachments": ["The medical records that support the service", "A signed designation of representative only when "
                          "appealing for the member"]}]},
    {"key": "n2", "group": "Anthem / BCBS", "request": "reconsideration_or_first_appeal", "variant": "fep",
     "when": "A Federal Employee Program member", "confidence": "low",
     "scope": "Federal Employee Program members (FEP has its own disputed-claim process, not covered in full)",
     "time_limit": "The FEP's disputed claims process (see the member's brochure); not covered by this research.",
     "channels": [
         {"channel": "phone", "name": "FEP Provider Services", "where": "800-382-5520", "minutes": 15, "sources": ["v12", "v13"]},
         {"channel": "mail", "name": "Federal Employee Program claims and appeals", "minutes": 20, "sources": ["v12", "v13"],
          "where": "Anthem Blue Cross and Blue Shield Federal Employee Program P.O. Box 105557 Atlanta, GA 30348-5557"}]},
    {"key": "n3", "group": "HealthSpring", "request": "reconsideration_or_first_appeal", "variant": "main", "confidence": "medium",
     "scope": "HealthSpring Medicare Advantage (Cigna's Medicare Advantage plans since January 1, 2026)",
     "time_limit": "180 days from the claim payment date.",
     "channels": [
         {"channel": "payer_portal", "name": "Availity, HealthSpring Medicare Advantage payer space (payer ID 52192)",
          "where": "https://www.availity.com", "minutes": 8, "sources": ["v35", "v36"]},
         {"channel": "fax", "name": "HealthSpring Reconsiderations", "where": "615-401-4642", "minutes": 12, "sources": ["v35", "v36"]},
         {"channel": "mail", "name": "HealthSpring Reconsiderations", "where": "HealthSpring Reconsiderations PO Box 1004 Nashville, TN 37202",
          "minutes": 18, "sources": ["v35", "v36"]}]},
    {"key": "n4", "group": "HealthSpring", "request": "second_level_appeal", "variant": "main", "confidence": "medium",
     "scope": "HealthSpring Medicare Advantage, participating providers: post-service appeal",
     "time_limit": "As the reconsideration decision states.",
     "channels": [
         {"channel": "fax", "name": "HealthSpring Appeals (post-service appeal and claim dispute form)", "where": "855-699-8985",
          "minutes": 14, "sources": ["v36"]},
         {"channel": "mail", "name": "HealthSpring Appeals (post-service appeal and claim dispute form)",
          "where": "HealthSpring Appeals PO Box 650065 Dallas, TX 75265", "minutes": 18, "sources": ["v36"]}]},
    {"key": "n5", "group": "HealthSpring", "request": "claim_status_or_reprocess", "variant": "main", "confidence": "medium",
     "scope": "HealthSpring Medicare Advantage", "time_limit": "None for a status check.",
     "channels": [{"channel": "payer_portal", "name": "Availity Claim Status (HealthSpring Medicare Advantage, payer ID 52192)",
                   "where": "https://www.availity.com", "minutes": 3, "sources": ["v35"]}]},
    {"key": "n6", "group": "Ambetter (Centene)", "request": "reconsideration_or_first_appeal", "variant": "clinical",
     "when": "Denied for medical necessity or authorization (the member appeal process)", "confidence": "medium",
     "scope": "Marketplace, Kentucky and Indiana: medical necessity and authorization denials go through the member appeal process",
     "time_limit": "180 calendar days from the notice of action.",
     "channels": [
         {"channel": "fax", "name": "Appeals and Grievances Department (filed for the member)", "where": "833-886-7956", "minutes": 12,
          "sources": ["v40", "v41"], "notes": "The provider may file for the member; written consent may be required."},
         {"channel": "mail", "name": "Appeals and Grievances Department (filed for the member)", "minutes": 18, "sources": ["v40", "v41"],
          "where": "Attn: Appeals and Grievances Department PO Box 10341 Van Nuys, CA 91410-0341"}]},
]

GAPS_ADDED = [
    "WPS J8: its IVR number and any telephone reopening were not confirmed in WPS's own sources; no WPS phone route is listed.",
    "Indiana Medicaid paper claim addresses differ between IHCP documents (Quick Reference Guide v14.1, May 2026, and the Claim "
    "Submission and Processing module, November 24, 2026).",
    "Minutes are rough estimates of biller time, not payer figures.",
]


def build(raw_path: str) -> dict:
    raw = json.load(open(raw_path, encoding="utf-8"))
    sources: dict[str, dict] = {}
    rows: list[dict] = []
    gaps: list[str] = []
    for cl in raw["result"]:
        p = PREFIX[cl["key"]]
        for s in cl["found"]["sources"]:
            host = urlparse(s["url"]).netloc
            sources[f"{p}{s['id']}"] = {"url": s["url"], "title": s["title"].strip(),
                                        "publisher": (s.get("publisher") or PUBLISHER.get(host) or host).strip(),
                                        "date": (s.get("published_or_updated") or "").strip()}
        gaps += [phone(g) for g in cl["found"]["gaps"]]
        for i, r in enumerate(cl["found"]["payers"]):
            key = f"{p}{i}"
            group = DUPLICATE_GROUPS.get(r["payer_group"], r["payer_group"])
            row = {"key": key, "group": group, "request": r["request"], "scope": phone(r["plan_scope"]),
                   "time_limit": phone(r["time_limit"]), "confidence": r["confidence"], "variant": "main", "when": "",
                   "channels": []}
            if r["payer_group"] in DUPLICATE_GROUPS:
                row["duplicate_of_group"] = r["payer_group"]
            if key in VARIANTS:
                row["variant"], row["when"] = VARIANTS[key]
            for ch in r["channels"]:
                c = {"rank": ch["rank"], "channel": CHANNEL_OF.get(ch["channel"], ch["channel"]), "name": phone(ch["name"]).strip(),
                     "where": phone((ch.get("where") or "").strip()), "form": (ch.get("form") or "").strip(),
                     "fields": [phone(x) for x in ch.get("fields") or []], "attachments": [phone(x) for x in ch.get("attachments") or []],
                     "minutes": ch.get("minutes"), "notes": phone((ch.get("notes") or "").strip()), "required": bool(ch.get("required")),
                     "sources": [f"{p}{n}" for n in ch.get("source_ids") or []]}
                if ch["channel"] == "availity" and "availity" not in c["name"].lower():
                    c["name"] = "Availity: " + c["name"]
                row["channels"].append(c)
            rows.append(row)
    # the duplicate rows: one set kept (the research wrote the same UHC Medicare Advantage rows under two names)
    seen = {(r["group"], r["request"], r["variant"]) for r in rows if "duplicate_of_group" not in r}
    dropped_dupes = [r["key"] for r in rows if "duplicate_of_group" in r and (r["group"], r["request"], r["variant"]) in seen]
    rows = [r for r in rows if r["key"] not in dropped_dupes]
    for r in rows:
        r.pop("duplicate_of_group", None)
    by_key = {r["key"]: r for r in rows}
    corrections = []
    for key, rank, change, kind, why, evidence in CORRECTIONS:
        row = by_key[key]
        change = dict(change)
        if rank == "add":
            c = {"rank": 7.5, "channel": change["channel"], "name": change["name"], "where": phone(change.get("where", "")),
                 "form": change.get("form", ""), "fields": change.get("fields", []), "attachments": change.get("attachments", []),
                 "minutes": change.get("minutes"), "notes": phone(change.get("notes", "")), "required": False,
                 "sources": list(change.get("sources", []))}
            # an added accepted channel goes after the research's accepted ones, before the not-accepted
            acc = [x["rank"] for x in row["channels"] if x["channel"] != "not_accepted"]
            c["rank"] = (max(acc) + 0.5) if acc else 0.5
            row["channels"].append(c)
            corrections.append({"route": key, "channel": c["name"], "change": kind, "why": why, "evidence": evidence})
            continue
        if rank is None:
            target = row
        else:
            target = next(x for x in row["channels"] if x["rank"] == rank)
        if change.pop("drop", False):
            row["channels"].remove(target)
            corrections.append({"route": key, "channel": target["name"], "change": "dropped", "why": why, "evidence": evidence})
            continue
        for k, v in change.items():
            if k == "notes_add":
                target["notes"] = (target.get("notes", "").rstrip() + " " + phone(v)).strip()
            elif k == "sources_add":
                target["sources"] = list(dict.fromkeys(list(target.get("sources", [])) + v))
            elif k == "sources_remove":
                p = key[0]
                target["sources"] = [s for s in target["sources"] if s not in {f"{p}{n}" for n in v}]
            elif k == "attachments_add":
                target["attachments"] = list(target.get("attachments", [])) + v
            elif k == "fields_replace":
                target["fields"] = [v.get(x, x) for x in target["fields"]]
            elif k == "unverified":
                target["unverified"] = phone(v)
            elif k == "time_limit_unverified":
                target["time_limit_verified"] = False
            else:
                target[k] = phone(v) if isinstance(v, str) else v
        corrections.append({"route": key, "channel": target.get("name") if rank is not None else None, "change": kind, "why": why,
                            "evidence": evidence})
    for nr in NEW_ROWS:
        row = {"key": nr["key"], "group": nr["group"], "request": nr["request"], "scope": nr["scope"], "time_limit": nr["time_limit"],
               "confidence": nr["confidence"], "variant": nr["variant"], "when": nr.get("when", ""), "channels": []}
        for i, ch in enumerate(nr["channels"], 1):
            row["channels"].append({"rank": i, "channel": ch["channel"], "name": ch["name"], "where": phone(ch.get("where", "")),
                                    "form": ch.get("form", ""), "fields": ch.get("fields", []), "attachments": ch.get("attachments", []),
                                    "minutes": ch.get("minutes"), "notes": phone(ch.get("notes", "")), "required": False,
                                    "sources": list(ch["sources"])})
        rows.append(row)
        corrections.append({"route": nr["key"], "channel": None, "change": "added",
                            "why": "A route the verifier found missing, with its sources.", "evidence": NEW_SOURCES[nr["channels"][0]["sources"][0]]["url"]})
    # VA Community Care: the practice's region (Kentucky and Indiana are Region 2) first
    for r in rows:
        if r["group"] == "VA Community Care" and r["variant"] == "main":
            for c in r["channels"]:
                m = re.search(r"Region 2:\s*([^;]+)", c["where"])
                if m:
                    c["notes"] = (c["notes"] + " " + VA_REGION2 + " Other regions: " + c["where"] + ".").strip()
                    c["where"] = m.group(1).strip()
                    c["sources"] = list(dict.fromkeys(c["sources"] + ["v24"]))
    corrections.append({"route": "VA Community Care", "channel": None, "change": "added", "evidence": NEW_SOURCES["v24"]["url"],
                        "why": "Kentucky and Indiana belong to CCN Region 2 (Optum): its number and address come first."})
    for k in dropped_dupes:
        corrections.append({"route": k, "channel": None, "change": "dropped", "evidence": "",
                            "why": "The research wrote the same UnitedHealthcare Medicare Advantage rows under two group names; one set kept."})
    sources.update(NEW_SOURCES)
    # ranks: the research's order, accepted channels first, then the not-accepted ones; each re-numbered 1..n
    for r in rows:
        acc = sorted([c for c in r["channels"] if c["channel"] != "not_accepted"], key=lambda c: c["rank"])
        nac = sorted([c for c in r["channels"] if c["channel"] == "not_accepted"], key=lambda c: c["rank"])
        for i, c in enumerate(acc + nac, 1):
            c["rank"] = i
            c["url"] = c.get("url", url_of(c["where"])) or ""
            why = c.pop("unverified", None)
            low = r["confidence"] == "low"
            c["verified"] = bool(c["sources"]) and not why and not low
            if why:
                c["unverified_why"] = why
            elif low:
                c["unverified_why"] = "The research rated this route's sources low (an older or second-hand source)."
            elif not c["sources"]:
                c["unverified_why"] = "No source."
        r["channels"] = acc + nac
        r.setdefault("time_limit_verified", r["confidence"] != "low")
        r["sources"] = sorted(set(r.get("sources", [])) | {s for c in r["channels"] for s in c["sources"]})
    used = {s for r in rows for c in r["channels"] for s in c["sources"]} | {s for r in rows for s in r["sources"]}
    out = {
        "checked": CHECKED,
        "about": ("How each payer takes each kind of request, easiest accepted way first, from the payers' own public pages "
                  "(research of 2026-11-28, checked a second time by an independent verifier; its corrections applied). Public "
                  "payer facts only. Minutes are rough estimates of biller time. Built by plans/tools/routes/build_payer_routes.py."),
        "requests": {
            "medicare_reopening": "Medicare reopening (a clerical error fixed without an appeal)",
            "medicare_redetermination": "Medicare redetermination (the first-level appeal)",
            "reconsideration_or_first_appeal": "Reconsideration or first-level appeal",
            "second_level_appeal": "Second-level appeal",
            "corrected_claim": "Corrected claim",
            "records_or_attachments": "Records or attachments the payer asked for",
            "overpayment_refund": "Overpayment refund",
            "claim_status_or_reprocess": "Claim status, or a request to reprocess",
            "timely_filing_proof": "Proof of timely filing",
        },
        "channels": {"payer_portal": "Payer portal", "clearinghouse": "Clearinghouse (EDI)", "phone": "Phone", "fax": "Fax",
                     "mail": "Mail", "email": "Email", "not_accepted": "Not accepted"},
        "groups": GROUPS,
        "not_covered": NOT_COVERED,
        "sources": {k: v for k, v in sorted(sources.items()) if k in used},
        "routes": rows,
        "gaps": gaps + GAPS_ADDED,
        "corrections": corrections,
    }
    return out


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    out = build(sys.argv[1])
    with open(sys.argv[2], "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    n = sum(len(r["channels"]) for r in out["routes"])
    print(f"{len(out['routes'])} routes, {n} channels, {len(out['sources'])} sources, {len(out['corrections'])} corrections")


if __name__ == "__main__":
    main()
