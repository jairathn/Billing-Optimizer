"""
Appeal packet generator.

Builds a ready-to-send reconsideration letter (plain text) plus an evidence
block and an attachment checklist for one finding. Letters are deterministic
templates: no patient data leaves the server and nothing is invented. The
clinical paragraph is a placeholder the biller fills from the chart.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from .codes import CARC, RARC

PRACTICE = {
    "name": "Owensboro Dermatology Associates, PSC",
    "address": "2821 New Hartford Rd, Owensboro, KY 42303",
    "tin": "31-1488176",
    "npi": "1821130295",
}

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


def _header(d: dict, subject: str) -> str:
    f = d["finding"]
    remit = d.get("remit") or {}
    return "\n".join([
        f"{date.today().isoformat()}",
        "",
        f"To: {remit.get('payer_name') or f.get('payer_name')}, Provider Appeals / Claim Reconsideration",
        f"From: {PRACTICE['name']}, {PRACTICE['address']}, TIN {PRACTICE['tin']}, Group NPI {PRACTICE['npi']}",
        f"Re: {subject}",
        f"Patient account (claim) number: {f.get('patient_control_number')}    Payer claim number: {(d.get('claim') or {}).get('payer_claim_control_number') or '[payer claim number]'}",
        f"Date of service: {f.get('service_date')}    Rendering provider NPI: {f.get('rendering_provider_npi') or '[NPI]'}",
        "",
    ])


def _closing(d: dict, ask: str) -> str:
    f = d["finding"]
    kind = f.get("payer_kind")
    legal = ""
    if kind in ("commercial", "supplement"):
        legal = ("Under Kentucky's prompt-payment statutes (KRS 304.17A-700 to 304.17A-730) a clean claim must be paid within 30 days and "
                 "unpaid amounts accrue interest; we request payment of interest on the reprocessed amount. If this plan is an employer self-funded "
                 "plan governed by ERISA, we request, as the member's authorized representative under the assignment of benefits, a copy of the "
                 "specific rule, guideline, protocol, or edit relied on for this determination (29 CFR 2560.503-1(g) and (h)).")
    elif kind == "medicare_advantage":
        legal = ("As a Medicare Advantage organization determination, this request is submitted within the reconsideration period. Medicare Advantage "
                 "plans must apply Medicare coverage and coding rules (42 CFR 422.101) and may not apply internal criteria that are more restrictive than "
                 "original Medicare.")
    elif kind == "medicare":
        legal = "This is a request for redetermination under 42 CFR 405.940 et seq., submitted within 120 days of the initial determination."
    return "\n".join([
        "",
        ask,
        "",
        legal,
        "",
        "Please reprocess the claim and issue payment with a corrected remittance. If the determination is upheld, provide the written rationale, "
        "the specific policy or edit relied on, and the next level of appeal with its deadline.",
        "",
        "Sincerely,",
        "",
        "____________________________",
        f"Billing Department, {PRACTICE['name']}",
        "Phone: [practice phone]    Fax: [practice fax]    Email: [billing email]",
    ])


def build_packet(d: dict, show_phi: bool = False) -> dict[str, Any]:
    f = d["finding"]
    sig = f["signal"]
    code = f.get("procedure_code") or ""
    orig = f.get("original_procedure_code") or (f.get("evidence") or {}).get("charge_implies")
    payer = f.get("payer_group") or f.get("payer_name")
    attachments = ["Copy of this letter", "Copy of the 835 remittance page for this claim (or the payer EOB)", "Copy of the submitted claim (CMS-1500 image or 837 print)"]
    corrected_claim = None

    if sig in ("DOWNCODE_SVC06", "DOWNCODE_CHARGE", "DOWNCODE_RATE", "DOWNCODE_LEVEL_DENIED"):
        subject = f"Request for reconsideration of evaluation and management level reduction, {orig or code} paid as {code}"
        body = "\n".join([
            f"We are writing to dispute the reduction of the office visit on the date of service above from CPT {orig or '[billed code]'} to CPT {code}.",
            "",
            f"The visit was coded under the AMA CPT office or other outpatient evaluation and management guidelines in effect on the date of service, "
            f"which select the level by medical decision making (MDM) or by total time on the date of the encounter. CPT {orig or '[billed code]'} "
            f"requires {EM_MDM.get(orig or '', '[level]')} MDM. The documentation, attached, supports that level as follows:",
            "",
            "  Number and complexity of problems addressed: [describe: for example two or more stable chronic conditions, or one chronic condition with exacerbation, or an undiagnosed new problem with uncertain prognosis]",
            "  Amount and complexity of data reviewed and analyzed: [describe: external records reviewed, tests ordered or reviewed, independent historian, discussion with another physician]",
            "  Risk of complications or morbidity of patient management: [describe: prescription drug management, decision regarding minor or major surgery, social determinants limiting treatment]",
            "  Total time on the date of the encounter, if used instead of MDM: [minutes and activities]",
            "",
            f"The claim was adjudicated at the {code} level without a request for records and without a stated clinical basis. An automated level "
            "reduction that does not review the medical record is not a valid determination of the level of service actually documented and rendered. "
            "We ask that the claim be reprocessed at the level billed, and that we be provided the written rationale and the edit or policy relied on "
            "if the reduction is upheld.",
        ])
        ask = f"Requested action: reprocess CPT {orig or '[billed code]'} as billed and pay the difference of {money(f.get('shortfall_cents'))} (expected allowed {money(f.get('expected_cents'))} versus {money(f.get('allowed_cents'))} allowed)."
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
    elif sig in ("DENIED_MEDICAL_NECESSITY", "DENIED_FREQUENCY", "DENIED_AUTH", "DENIED_PROVIDER", "DENIED_NONCOVERED", "DENIED_OTHER", "CLAIM_DENIED_UNPAID", "DENIED_DUPLICATE_UNPAID", "DENIED_TIMELY_FILING", "DENIED_MISSING_PRIMARY"):
        subject = f"Request for reconsideration of denial: {code or 'claim'} ({f.get('carc')} {CARC.get(f.get('carc') or '', '')})"
        reasons = {
            "DENIED_MEDICAL_NECESSITY": "The service was medically necessary and is covered under the applicable coverage policy. The attached records document the diagnosis, the indication, the treatment plan and the clinical findings that support the service: [summarize].",
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
            reasons.get(sig, reasons["DENIED_OTHER"]),
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

    letter = _header(d, subject) + body + _closing(d, ask)
    return {
        "finding_id": f["finding_id"], "signal": sig, "subject": subject, "letter": letter,
        "corrected_claim_instructions": corrected_claim,
        "attachments": attachments,
        "evidence": _evidence_block(d),
        "deadline": f.get("appeal_deadline"),
        "payer_contact": {"phone": (d.get("remit") or {}).get("payer_contact_phone"), "url": (d.get("remit") or {}).get("payer_contact_url")},
    }
