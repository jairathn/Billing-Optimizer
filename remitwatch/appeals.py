"""
Appeal packet generator.

Builds a ready-to-send reconsideration letter (plain text) plus an evidence
block and an attachment checklist for one finding. Without a clinical note the
letter is a deterministic template with bracketed prompts for the biller. When
remitwatch.notes has reviewed the note, its paragraphs replace the prompts.

Statute wording comes from remitwatch.codes.LEGAL, which was checked against
the Kentucky and Indiana primary sources. Filing windows come from
remitwatch.codes.appeal_rule.
"""
from __future__ import annotations

import os
from datetime import date
from typing import Any

from .codes import CARC, LEGAL, RARC, appeal_rule, member_number

# Who the letters come from. Set PRACTICE_NAME, PRACTICE_ADDRESS, PRACTICE_TIN and PRACTICE_NPI (and the contact lines)
# in the host's settings: until then the letters carry the bracketed words, which shows at a glance what is missing.
PRACTICE = {
    "name": os.environ.get("PRACTICE_NAME", "[practice name]"),
    "address": os.environ.get("PRACTICE_ADDRESS", "[practice address]"),
    "tin": os.environ.get("PRACTICE_TIN", "[practice tax ID]"),
    "npi": os.environ.get("PRACTICE_NPI", "[group NPI]"),
    "phone": os.environ.get("PRACTICE_PHONE", "[practice phone]"),
    "fax": os.environ.get("PRACTICE_FAX", "[practice fax]"),
    "email": os.environ.get("PRACTICE_EMAIL", "[billing email]"),
}

DOWNCODE_LETTER_SIGNALS = ("DOWNCODE_SVC06", "DOWNCODE_VS_BILLED", "DOWNCODE_CHARGE", "DOWNCODE_RATE", "DOWNCODE_LEVEL_REDUCED",
                           "DOWNCODE_LINE_SWAP", "DOWNCODE_LEVEL_DENIED")

EM_MDM = {
    "99212": "straightforward", "99213": "low", "99214": "moderate", "99215": "high",
    "99202": "straightforward", "99203": "low", "99204": "moderate", "99205": "high",
}


def money(c: int | None) -> str:
    return f"${(c or 0) / 100:,.2f}"


def _evidence_block(d: dict) -> str:
    f = d["finding"]
    remit = d.get("remit") or {}
    claim = d.get("claim") or {}
    lines = d.get("lines") or []
    adjs = d.get("adjustments") or []
    rems = d.get("remarks") or []
    out = []
    out.append("REMITTANCE (X12 835) EVIDENCE")
    out.append(f"  Payer: {remit.get('payer_name') or f.get('payer_name')}   Payer ID: {remit.get('payer_id') or ''}")
    out.append(f"  Check/EFT trace number (TRN02): {remit.get('trace_number') or ''}   Payment date: {remit.get('payment_date') or f.get('payment_date')}")
    out.append(f"  Payer claim control number (CLP07): {claim.get('payer_claim_control_number') or ''}   Patient control number (CLP01): {f.get('patient_control_number')}")
    out.append(f"  Date of service: {f.get('service_date')}   Rendering provider NPI: {f.get('rendering_provider_npi') or ''}   Claim status (CLP02): {claim.get('claim_status_code') or ''}")
    out.append("  Service lines:")
    adj_by_line: dict[str, list] = {}
    for a in adjs:
        adj_by_line.setdefault(a.get("line_key") or "claim", []).append(a)
    rem_by_line: dict[str, list] = {}
    for r in rems:
        rem_by_line.setdefault(r.get("line_key") or "claim", []).append(r)
    for ln in lines:
        mark = "  >>" if ln["line_key"] == f.get("line_key") else "    "
        mods = ",".join(__import__("json").loads(ln["modifiers"])) if ln.get("modifiers") else ""
        orig = f" (submitted as {ln['original_procedure_code']})" if ln.get("original_procedure_code") else ""
        out.append(f"{mark} {ln['procedure_code']}{'-' + mods if mods else ''}{orig}  charge {money(ln['charge_cents'])}  allowed {money(ln['allowed_cents'])}  paid {money(ln['paid_cents'])}")
        for a in adj_by_line.get(ln["line_key"], []):
            out.append(f"         CAS {a['group_code']}-{a['reason_code']} {money(a['amount_cents'])}: {CARC.get(a['reason_code'], '')}")
        for r in rem_by_line.get(ln["line_key"], []):
            out.append(f"         RARC {r['code']}: {RARC.get(r['code'], '')}")
    for a in adj_by_line.get("claim", []):
        out.append(f"    claim-level CAS {a['group_code']}-{a['reason_code']} {money(a['amount_cents'])}: {CARC.get(a['reason_code'], '')}")
    ev = f.get("evidence") or {}
    if ev.get("basis"):
        out.append(f"  Expected amount basis: {ev['basis']}")
    if f.get("expected_cents"):
        out.append(f"  Expected allowed: {money(f['expected_cents'])}   Actual allowed: {money(f.get('allowed_cents'))}   Shortfall: {money(f.get('shortfall_cents'))}")
    ema = d.get("ema") or []
    billed = [e for e in ema if e.get("family") == "charge_lines" and e.get("activity_type") == "Charge"]
    if billed:
        out.append("  Practice management system (ModMed EMA) billed lines for this claim:")
        for e in billed[:12]:
            out.append(f"    {e.get('service_date')} {e.get('cpt')}{'-' + e['modifiers'].strip('[]').replace(chr(34), '') if e.get('modifiers') else ''} units {e.get('units')} charge {money(e.get('charges_cents'))} status {e.get('claim_status') or ''}")
    return "\n".join(out)


def _medicare_route(f: dict) -> str:
    """How a Medicare Part B finding is worked, as its filing date was set (detect, codes.CORRECTED_RULES): a new claim
    for one returned as unprocessable (MA130), a reopening for a clerical error, else a redetermination."""
    if f.get("actionable") == "correct_and_resubmit":
        basis = f.get("deadline_basis") or ""
        if "MA130" in basis:
            return "new_claim"
        if "reopening" in basis and "takes no documents" not in basis:
            return "reopening"
    return "redetermination"


MEDICARE_NEXT = {
    "reopening": "Medicare Part B takes no corrected claims. Fix it by asking the Medicare contractor for a reopening (a clerical error) "
                 "through its portal, by phone or in writing, within a year of the remittance.",
    "new_claim": "Medicare returned the claim as unprocessable (MA130), so it has no appeal rights. Fix what the remark codes name and send "
                 "it as a new claim, not a corrected claim, within a year of the date of service.",
    "redetermination": "Send Medicare a redetermination request with the records or information the remark asks for, within 120 days "
                       "of the remittance. A reopening takes no documents, and Medicare Part B takes no corrected claims.",
}


def next_step(f: dict) -> str | None:
    """What to do, where the payer's own route differs from the plain label: a Medicare Part B fix goes by its route."""
    if f.get("payer_kind") == "medicare" and f.get("actionable") == "correct_and_resubmit":
        return MEDICARE_NEXT[_medicare_route(f)]
    return None


def _process(f: dict) -> str:
    """Name the payer's own first-level process so the request lands in the right queue."""
    grp, kind = f.get("payer_group") or "", f.get("payer_kind")
    if grp.startswith("Anthem") and kind in ("commercial", "supplement", "other"):
        return ("This is a Claim Payment Reconsideration, the first step of the Claim Payment Dispute process in the Anthem provider manual, "
                "filed within the two years the manual allows from the issue date of the Explanation of Payment.")
    if kind == "medicare":
        route = _medicare_route(f)
        if route == "reopening":
            return ("This is a request to reopen the initial determination shown on the remittance advice to correct a clerical error, "
                    "made within one year of that determination (42 CFR 405.980(b)(1)).")
        if route == "new_claim":
            return ("The claim was returned as unprocessable (remark code MA130), which carries no appeal rights; "
                    "it is being sent again as a new claim with the error corrected.")
        return "This is a request for redetermination of the initial determination shown on the remittance advice."
    if kind == "medicare_advantage":
        return "This is a contracted provider payment dispute filed under the plan's provider manual."
    return "This is a first-level provider payment dispute (reconsideration) filed under the plan's provider manual."


def _desk(f: dict) -> str:
    """Who at the payer the letter is for: Medicare's first level is a redetermination (or a reopening), not a reconsideration."""
    if f.get("payer_kind") == "medicare":
        return {"reopening": "Reopenings", "new_claim": "Claims"}.get(_medicare_route(f), "Redeterminations")
    return "Provider Appeals / Claim Reconsideration"


def _medicare_subject(f: dict, subject: str) -> str:
    """A Medicare Part B letter names the level it is: a redetermination or a reopening, never a reconsideration (the QIC's)."""
    if f.get("payer_kind") != "medicare":
        return subject
    word = {"reopening": "Request to reopen", "new_claim": "New claim (the original was returned as unprocessable)"}.get(
        _medicare_route(f), "Request for redetermination")
    for old in ("Request for reconsideration of denial", "Request for reconsideration of", "Request for reconsideration"):
        if subject.startswith(old):
            return word + subject[len(old):]
    return subject


def _header(d: dict, subject: str, show_phi: bool = False) -> str:
    f = d["finding"]
    remit = d.get("remit") or {}
    claim = d.get("claim") or {}
    patient = "[patient name]    Member ID: [member ID]"
    if show_phi and (claim.get("patient_last") or claim.get("patient_id")):
        # only a number the payer labelled a member number: a Social Security number or tax ID is left for the biller to replace
        patient = f"{claim.get('patient_first') or ''} {claim.get('patient_last') or ''}".strip() + f"    Member ID: {member_number(claim) or '[member ID]'}"
    return "\n".join([
        f"{date.today().isoformat()}",
        "",
        f"To: {remit.get('payer_name') or f.get('payer_name')}, {_desk(f)}",
        f"From: {PRACTICE['name']}, {PRACTICE['address']}, TIN {PRACTICE['tin']}, Group NPI {PRACTICE['npi']}",
        f"Re: {subject}",
        f"Patient: {patient}",
        f"Patient account (claim) number: {f.get('patient_control_number') or '[claim number]'}    Payer claim number: {claim.get('payer_claim_control_number') or '[payer claim number]'}",
        f"Date of service: {f.get('service_date') or '[date of service]'}    Rendering provider: {f.get('rendering_provider_name') or ''} NPI {f.get('rendering_provider_npi') or '[NPI]'}",
        f"Remittance: trace number {remit.get('trace_number') or '[check or EFT number]'}, dated {remit.get('payment_date') or f.get('payment_date') or '[date]'}",
        "",
        _process(f),
        "",
        "",
    ])


def _closing(d: dict, ask: str) -> str:
    f = d["finding"]
    kind, sig = f.get("payer_kind"), f.get("signal") or ""
    legal: list[str] = []
    if kind in ("commercial", "supplement", "other"):
        if sig in ("UNDERPAID_VS_OWN_RATE", "MPPR_EXCESS"):
            legal.append("To the extent this coverage is subject to Kentucky insurance law: " + LEGAL["KRS_708"])
        if sig == "TAKEBACK":
            legal.append("To the extent this coverage is subject to Kentucky insurance law: " + LEGAL["KRS_714"])
        legal.append("To the extent this coverage is subject to Kentucky insurance law: " + LEGAL["KRS_702"] + " " + LEGAL["KRS_730"]
                     + " We request interest on the amount paid on reprocessing.")
        if sig in DOWNCODE_LETTER_SIGNALS or sig == "CODE_CHANGED":
            legal.append("If the member's coverage is issued in Indiana: " + LEGAL["IC_6_5"] + " The remittance shows no request for, and no "
                         "review of, the medical record before the code or payment level was changed.")
        legal.append(LEGAL["ERISA"])
    elif kind == "medicare_advantage":
        legal.append(LEGAL["MA"])
    elif kind == "medicare" and _medicare_route(f) == "redetermination":
        legal.append(LEGAL["MEDICARE"])
    return "\n".join([
        "",
        ask,
        "",
        "\n\n".join(legal),
        "",
        "Please reprocess the claim and issue payment with a corrected remittance. If the determination is upheld, provide the written rationale, "
        "the specific policy or edit relied on, and the next level of appeal with its deadline.",
        "",
        "Sincerely,",
        "",
        "____________________________",
        f"Billing Department, {PRACTICE['name']}",
        f"Phone: {PRACTICE['phone']}    Fax: {PRACTICE['fax']}    Email: {PRACTICE['email']}",
    ])


def build_packet(d: dict, show_phi: bool = False, clinical: list[str] | None = None) -> dict[str, Any]:
    """`clinical` is the list of paragraphs written from the reviewed note; without it the letter carries prompts in brackets."""
    f = d["finding"]
    sig = f["signal"]
    code = f.get("procedure_code") or ""
    ev0 = f.get("evidence") or {}
    orig = f.get("original_procedure_code") or ev0.get("billed_code") or ev0.get("charge_implies")
    if orig == code:
        orig = None
    payer = f.get("payer_group") or f.get("payer_name")
    attachments = ["Copy of this letter", "Copy of the 835 remittance page for this claim (or the payer EOB)", "Copy of the submitted claim (CMS-1500 image or 837 print)"]
    corrected_claim = None

    if sig in DOWNCODE_LETTER_SIGNALS:
        billed = orig or code
        kept_code = sig in ("DOWNCODE_LEVEL_REDUCED", "DOWNCODE_RATE", "DOWNCODE_LEVEL_DENIED") or not orig
        like = ev0.get("paid_like")
        if sig == "DOWNCODE_LEVEL_DENIED":
            subject = f"Request for reconsideration: office visit {billed} denied as not supported at the level billed ({f.get('carc') or f.get('rarc') or 'level of service'})"
            what = f"We dispute the denial of the office visit, CPT {billed}, on the date of service above for the stated reason that the level of service is not supported."
        elif sig == "DOWNCODE_LINE_SWAP":
            subject = f"Request for reconsideration: office visit {billed} denied and a lower level paid in its place"
            what = (f"We dispute the denial of the office visit billed as CPT {billed} and the payment of CPT {ev0.get('paid_line_code') or '[lower level]'} "
                    "in its place on the date of service above.")
        elif kept_code:
            subject = f"Request for reconsideration: payment for office visit {billed} reduced for level of care ({f.get('carc') or 'reason code 186'})"
            what = (f"We dispute the reduction in payment for the office visit, CPT {billed}, on the date of service above. The remittance keeps the code we billed "
                    f"and reduces the allowed amount with a level of care adjustment{', to the amount the plan pays for CPT ' + like if like else ''}.")
        else:
            subject = f"Request for reconsideration of evaluation and management level reduction, {billed} paid as {code}"
            what = f"We dispute the reduction of the office visit on the date of service above from CPT {billed} to CPT {code}."
        if clinical:
            support = ["The documentation, attached, supports the level billed as follows:", ""] + [("  " + p) for p in clinical]
        else:
            support = [
                "The documentation, attached, supports that level as follows:",
                "",
                "  Number and complexity of problems addressed: [describe: for example two or more stable chronic conditions, or one chronic condition with exacerbation, or an undiagnosed new problem with uncertain prognosis]",
                "  Amount and complexity of data reviewed and analyzed: [describe: external records reviewed, tests ordered or reviewed, independent historian, discussion with another physician]",
                "  Risk of complications or morbidity of patient management: [describe: prescription drug management, decision regarding minor or major surgery, social determinants limiting treatment]",
                "  Total time on the date of the encounter, if used instead of MDM: [minutes and activities]",
            ]
        anthem = (f.get("payer_group") or "").startswith("Anthem") and f.get("payer_kind") in ("commercial", "supplement", "other")
        method = ("The plan's provider manual states that it reviews evaluation and management claims before payment to decide whether the level submitted "
                  "is higher than the level supported on the claim. A claim carries diagnosis codes. It does not carry the problems addressed, the data "
                  "reviewed or the risk of management, which are the elements that set the level under CPT. The level of this visit can only be judged "
                  "from the medical record, and the record was not requested." if anthem else
                  "The claim was adjudicated at a lower level without a request for records and without a stated clinical basis. A level reduction made "
                  "from claim data alone is not a determination of the service that was documented and rendered.")
        body = "\n".join([
            what,
            "",
            f"The visit was coded under the AMA CPT office or other outpatient evaluation and management guidelines in effect on the date of service, "
            f"which select the level by medical decision making (MDM) or by total time on the date of the encounter. CPT {billed} "
            f"requires {EM_MDM.get(billed, '[level]')} MDM. " + support[0],
            *support[1:],
            "",
            method,
            "",
            "We ask that the claim be reprocessed at the level billed, and that we be given the written rationale and the edit or policy relied on if the reduction is upheld.",
        ])
        ask = f"Requested action: reprocess CPT {billed} as billed and pay the difference of {money(f.get('shortfall_cents'))} (expected allowed {money(f.get('expected_cents'))} versus {money(f.get('allowed_cents'))} allowed)."
        attachments += ["Complete progress note for the date of service, signed", "Any orders, results, outside records and prescriptions referenced in the note", "AMA CPT E/M MDM table (2021 guidelines) with the applicable rows highlighted"]
    elif sig == "CODE_CHANGED":
        subject = f"Request for reconsideration: procedure code changed by the payer from {orig} to {code}"
        body = "\n".join([
            f"The service on the date of service above was billed as CPT {orig} and adjudicated as CPT {code} (the remittance reports the submitted code in SVC06). "
            "No request for records preceded the change and no rationale was provided.",
            "",
            f"CPT {orig} correctly describes the service performed and documented: [describe the procedure, site, size or stage, and why {orig} rather than {code} applies]. "
            "A payer may not unilaterally recode a service without reviewing the documentation; if the plan maintains that a different code applies, "
            "we ask for the written clinical and coding rationale.",
            "",
            f"We ask that the line be reprocessed as CPT {orig} as billed.",
        ])
        ask = f"Requested action: reprocess CPT {orig} as billed and pay the difference of {money(f.get('shortfall_cents'))}."
        attachments += ["Procedure or operative note for the date of service", "Pathology report if applicable"]
    elif sig in ("EM_BUNDLED_MOD25", "EM_BUNDLED_MOD57_MAJOR", "EM_BUNDLED_MOD24"):
        mod = "25" if sig == "EM_BUNDLED_MOD25" else ("57" if "57" in sig else "24")
        same_day = ", ".join((f.get("evidence") or {}).get("same_day_codes") or [])
        subject = f"Request for reconsideration: evaluation and management service {code}-{mod} denied as included in a procedure ({f.get('carc')})"
        if mod == "25":
            rationale = ("Modifier 25 identifies a significant, separately identifiable evaluation and management service by the same physician on the same "
                         "day as a procedure. Under the CMS National Correct Coding Initiative Policy Manual, Chapter I, the decision to perform a minor "
                         "procedure is included in the procedure, but the evaluation and management of a separate problem, or work significantly beyond "
                         "the usual pre-procedure work, is separately reportable with modifier 25. The documentation shows a separate problem was "
                         "evaluated and managed: [describe the separate problem(s), history, exam, assessment and plan distinct from the procedure].")
        elif mod == "57":
            rationale = ("Modifier 57 identifies the evaluation and management service at which the decision to perform a major surgery (90-day global) "
                         f"was made, on the day of or the day before the procedure ({same_day}). The global surgical package does not include the visit "
                         "at which the decision for surgery was made. The note documents the evaluation and the decision for surgery: [describe].")
        else:
            rationale = ("Modifier 24 identifies an unrelated evaluation and management service during a postoperative period. The visit addressed a "
                         "condition unrelated to the prior procedure: [describe the unrelated problem and its management].")
        if clinical:
            rationale = rationale.split(": [describe")[0].rstrip(".") + ". The note documents the following:\n\n" + "\n".join("  " + p for p in clinical)
        body = "\n".join([
            f"The office visit {code}-{mod} on the date of service above was denied as included in another service on the same day ({same_day or 'see remittance'}), "
            f"reason code {f.get('carc')}: {CARC.get(f.get('carc') or '', '')}.",
            "",
            rationale,
            "",
            "The attached documentation supports a separately payable service. We ask that the line be reprocessed and paid.",
        ])
        ask = f"Requested action: reprocess line {code}-{mod} and pay the allowed amount of {money(f.get('expected_cents'))}."
        attachments += ["Complete progress note for the date of service showing the separate E/M work", "Procedure note for the same-day procedure"]
    elif sig in ("PROCEDURE_BUNDLED", "ADDON_BUNDLED"):
        same_day = ", ".join((f.get("evidence") or {}).get("same_day_codes") or [])
        subject = f"Request for reconsideration: {code} denied as included in another service ({f.get('carc')})"
        body = "\n".join([
            f"CPT {code} on the date of service above was denied as included in another service ({same_day or 'see remittance'}), reason code {f.get('carc')}: {CARC.get(f.get('carc') or '', '')}.",
            "",
            ("This code is an add-on code reported in addition to its primary procedure; add-on codes are not subject to multiple procedure reduction and are payable with the primary service (CPT Appendix D; CMS MPFS status indicator). " if sig == "ADDON_BUNDLED" else
             "There is no National Correct Coding Initiative procedure-to-procedure edit that bundles this service into the other services on this claim, or the edit allows a modifier and the services were distinct (separate lesion, separate site, or separate session) as documented. Mohs micrographic surgery stages (17311 to 17315) and reconstructive repairs or flaps performed by the same surgeon are separately reportable under CPT and NCCI guidance. "),
            "[Describe the lesion(s), site(s), stage(s) and the distinct work performed.]",
            "",
            "We ask that the line be reprocessed and paid.",
        ])
        ask = f"Requested action: reprocess CPT {code} and pay the allowed amount of {money(f.get('expected_cents'))}."
        attachments += ["Operative or procedure note(s) for the date of service", "Pathology report(s) if applicable", "NCCI PTP edit table excerpt for the code pair (showing no edit, or modifier indicator 1)"]
    elif sig in ("DENIED_MEDICAL_NECESSITY", "DENIED_LEVEL_OF_SERVICE", "DENIED_FREQUENCY", "DENIED_AUTH", "DENIED_PROVIDER", "DENIED_NONCOVERED", "DENIED_OTHER", "CLAIM_DENIED_UNPAID", "DENIED_DUPLICATE_UNPAID", "DENIED_TIMELY_FILING", "DENIED_MISSING_PRIMARY"):
        subject = f"Request for reconsideration of denial: {code or 'claim'} ({f.get('carc')} {CARC.get(f.get('carc') or '', '')})"
        reasons = {
            "DENIED_MEDICAL_NECESSITY": "The service was medically necessary and is covered under the applicable coverage policy. The attached records document the diagnosis, the indication, the treatment plan and the clinical findings that support the service: [summarize].",
            "DENIED_LEVEL_OF_SERVICE": "The plan states that the information submitted does not support the service as billed. The attached records document the lesion, the site, the indication and each stage or component of the procedure performed: [summarize].",
            "DENIED_FREQUENCY": "The number of units or frequency billed reflects the services actually rendered and documented: [describe counts, sites, fractions or sessions and the policy that allows them].",
            "DENIED_AUTH": "The service [was authorized under reference number ___ / did not require prior authorization under the plan's policy / was urgent and authorization could not be obtained in advance]. We request retroactive authorization and reprocessing.",
            "DENIED_PROVIDER": "The rendering provider is enrolled and credentialed with the plan [effective date ___, provider ID ___]. We request that the provider record be corrected and the claim reprocessed.",
            "DENIED_NONCOVERED": "The service is a covered benefit under the member's plan [cite the plan document or coverage policy]. If the plan maintains the service is not covered, provide the specific exclusion and confirm the member may be billed.",
            "DENIED_DUPLICATE_UNPAID": "This service was denied as a duplicate; however, no payment was ever issued for it on any remittance. Please identify the original payment (check number and date) or reprocess the line for payment.",
            "DENIED_TIMELY_FILING": "The claim was submitted within the filing limit. Proof of timely submission is attached (clearinghouse acceptance report / 277CA with date). We request that the timely-filing denial be reversed.",
            "DENIED_MISSING_PRIMARY": "The qualifying primary service was rendered and billed on the same claim; if the primary line was denied, that denial is also being appealed. Please reprocess both lines together.",
            "DENIED_OTHER": "[State the basis for reconsideration.]",
            "CLAIM_DENIED_UNPAID": "[State the basis for reconsideration and attach the corrected claim if the denial was for a correctable error.]",
        }
        body = "\n".join([
            f"The service(s) on the date of service above were denied with reason code {f.get('carc')}: {CARC.get(f.get('carc') or '', '')}"
            + (f"; remark {f.get('rarc')}: {RARC.get((f.get('rarc') or '').split(',')[0], '')}" if f.get("rarc") else "") + ".",
            "",
            (reasons.get(sig, reasons["DENIED_OTHER"]).split(": [summarize]")[0].rstrip(".") + ". The records show:\n\n" + "\n".join("  " + p for p in clinical))
            if clinical else reasons.get(sig, reasons["DENIED_OTHER"]),
            "",
            "We ask that the claim be reprocessed and paid.",
        ])
        ask = f"Requested action: reprocess and pay the expected allowed amount of {money(f.get('expected_cents'))}."
        attachments += ["Complete medical record for the date of service", "Any applicable coverage policy or LCD with the relevant criteria highlighted"]
        if sig == "DENIED_AUTH":
            attachments.append("Authorization confirmation or portal screenshot, if any")
        if sig == "DENIED_TIMELY_FILING":
            attachments.append("Clearinghouse acceptance report or 277CA showing the original submission date")
    elif sig in ("UNDERPAID_VS_OWN_RATE", "MPPR_EXCESS"):
        ev = f.get("evidence") or {}
        subject = f"Request for payment reconciliation: {code} allowed below the contracted/usual rate"
        body = "\n".join([
            f"CPT {code} on the date of service above was allowed at {money(f.get('allowed_cents'))}. The plan's usual allowed amount for this code and provider type is "
            f"{money(f.get('expected_cents'))} ({ev.get('basis', '')}).",
            "",
            ("This line was reduced as a multiple procedure beyond the plan's published multiple procedure policy "
             f"(paid at {int(100 * float(ev.get('pct_of_usual', 0)))}% of the usual allowed as procedure #{ev.get('rank')}). " if sig == "MPPR_EXCESS" else
             "No multiple-procedure reduction, modifier, or other adjustment reason reported on the remittance explains the lower allowed amount. "),
            "Please reconcile the allowed amount to the fee schedule in effect on the date of service and pay the difference, or provide the fee schedule amount and the basis for the reduction.",
        ])
        ask = f"Requested action: pay the difference of {money(f.get('shortfall_cents'))}."
        attachments += ["Contract fee schedule excerpt for this code, if available"]
    elif sig == "TAKEBACK":
        subject = f"Dispute of overpayment recoupment of {money(f.get('shortfall_cents'))} (remittance reference {ev0.get('reference') or '[reference]'})"
        body = "\n".join([
            f"The remittance above withheld {money(f.get('shortfall_cents'))} from payments due to this practice as recovery of an alleged overpayment "
            f"(provider-level adjustment WO, reference {ev0.get('reference') or '[reference]'}).",
            "",
            "We have no record of a written notice that identifies the covered person, the date of service, the claim reference and the basis for the "
            "overpayment determination, or we dispute the determination for the reason stated here: [state reason, or state that no notice was received].",
            "",
            "We ask that the withheld amount be returned until proper notice is given and any dispute is resolved, and that the claim-level detail of the "
            "recoupment be sent to us.",
        ])
        ask = f"Requested action: return the {money(f.get('shortfall_cents'))} withheld and provide the claim-level detail and basis for the recoupment."
        attachments += ["Copy of the overpayment notice, if one was received", "Proof of the original correct payment"]
    elif sig == "FEE_SKIM":
        subject = "Request for standard ACH electronic funds transfer with no payment fees"
        body = "\n".join([
            f"Remittances from this plan or its payment vendor withheld payment fees from amounts due to this practice ({f.get('title')}).",
            "",
            "We did not agree to a fee-based payment service. We request delivery of all payments by the standard health care electronic funds transfer "
            "(ACH CCD+ with the reassociation trace number, 45 CFR 162.1601 and 162.1602), with no enrollment in any virtual card or fee-based "
            "payment product, and written confirmation of the date the change takes effect. Our banking details will be supplied through your "
            "standard EFT enrollment process.",
        ])
        ask = "Requested action: move this practice to standard ACH EFT with no fees, and refund the fees withheld to date."
        attachments = ["Copy of this letter", "Remittance pages showing the fee adjustments (PLB segments)"]
    else:
        subject = f"Request for reconsideration: {f.get('title')}"
        body = "[State the basis for reconsideration.]"
        ask = f"Requested action: reprocess and pay {money(f.get('shortfall_cents'))}."

    # correctable errors come with instructions instead of an appeal
    if f.get("actionable") == "correct_and_resubmit":
        fixes = {
            "EM_BUNDLED_NO_MODIFIER": f"Append modifier 25 to {code} (if the E/M was significant and separately identifiable from the same-day procedure) and submit a corrected claim (frequency code 7, original payer claim number in the reference field).",
            "EM_BUNDLED_MOD57_MINOR": f"Replace modifier 57 with modifier 25 on {code}: the same-day procedure has a 0- or 10-day global period, so modifier 57 does not apply. Submit a corrected claim (frequency code 7).",
            "DENIED_MISSING_MODIFIER": f"Add the missing or correct modifier to {code} as indicated by the remark code ({f.get('rarc')}) and submit a corrected claim (frequency code 7).",
            "DENIED_CODING": f"Correct the code or modifier combination on {code} ({f.get('carc')} {f.get('rarc') or ''}) and submit a corrected claim (frequency code 7).",
            "DENIED_MISSING_INFO": f"Supply the missing information named by the remark code ({f.get('rarc')}) and resubmit (corrected claim or attachment, per the payer's instructions).",
            "CLAIM_DENIED_UNPAID": "Correct the error named by the reason and remark codes and resubmit the claim.",
        }
        corrected_claim = fixes.get(sig, "Correct the claim and resubmit.")
        if f.get("payer_kind") == "medicare":
            # Medicare Part B takes no corrected claims (frequency code 7): a clerical error is fixed by a reopening, a claim
            # returned as unprocessable by a new claim, and anything that needs records by a redetermination
            route = _medicare_route(f)
            what = {
                "EM_BUNDLED_NO_MODIFIER": f"Add modifier 25 to {code} (if the visit was significant and separately identifiable from the same-day procedure).",
                "EM_BUNDLED_MOD57_MINOR": f"Change modifier 57 to 25 on {code}: the same-day procedure has a 0- or 10-day global period.",
                "DENIED_MISSING_MODIFIER": f"Add or correct the modifier on {code} that the remark code names ({f.get('rarc') or 'see the remittance'}).",
                "DENIED_CODING": f"Correct the code or modifier combination on {code} ({f.get('carc') or ''} {f.get('rarc') or ''}).".replace("( ", "(").replace(" )", ")"),
            }.get(sig, "Correct the error the reason and remark codes name.")
            if route == "new_claim":
                corrected_claim = (f"Medicare returned this claim as unprocessable (MA130), so it has no appeal rights. Fix what the remark codes name "
                                   f"({f.get('rarc') or 'see the remittance'}) and send it as a new claim (frequency code 1, not a corrected claim) "
                                   "within 1 calendar year of the date of service.")
            elif route == "reopening":
                corrected_claim = (f"{what} Medicare Part B does not take corrected claims, so do not send one: ask the Medicare contractor for a "
                                   "reopening to correct the clerical error, through its provider portal, by phone or in writing, within 1 year of "
                                   "the initial determination.")
            else:
                corrected_claim = (f"Send a redetermination request with the information or records the remark code asks for "
                                   f"({f.get('rarc') or f.get('carc') or 'see the remittance'}) within 120 days of receiving the remittance. "
                                   "A reopening takes no documents, and Medicare Part B does not accept corrected claims.")

    subject = _medicare_subject(f, subject)
    letter = _header(d, subject, show_phi) + body + _closing(d, ask)
    rule = appeal_rule(f.get("payer_group"), f.get("payer_kind"), f.get("plan"))
    if (f.get("payer_group") or "").startswith("Anthem") and f.get("payer_kind") in ("commercial", "supplement", "other"):
        attachments.append("Anthem asks every dispute to include: a position statement (this letter), the provider name, address, phone, email and NPI or TIN, "
                           "the member's name and Anthem ID, the Anthem claim number and date of service, and all supporting documentation")
    return {
        "finding_id": f.get("finding_id"), "signal": sig, "subject": subject, "letter": letter,
        "deadline_basis": f.get("deadline_basis") or rule["label"], "process": _process(f),
        "corrected_claim_instructions": corrected_claim,
        "attachments": attachments,
        "evidence": _evidence_block(d),
        "deadline": f.get("appeal_deadline"),
        "payer_contact": {"phone": (d.get("remit") or {}).get("payer_contact_phone"), "url": (d.get("remit") or {}).get("payer_contact_url")},
    }
