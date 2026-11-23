"""Leverage matrix, part A: shared vocabulary, traditional Medicare (by MAC) and Medicare Advantage."""


def A(duty, days, unit, frm, rule, note=None):
    d = {'duty': duty, 'days': days, 'unit': unit, 'from': frm, 'rule': rule}
    if note:
        d['note'] = note
    return d


def E(step, opens, rules, decision, note=None):
    d = {'step': step, 'opens': opens, 'rules': rules if isinstance(rules, list) else [rules], 'decision': decision}
    if note:
        d['note'] = note
    return d


def R(what, rule, basis='right'):
    return {'what': what, 'rule': rule, 'basis': basis}


def D(item, rule):
    return {'item': item, 'rule': rule}


ISSUE_KINDS = {
    'denial_to_appeal': {
        'label': 'Denial to appeal (not medical necessity, not coding)',
        'remitwatch_signals': ['DENIED_OTHER', 'DENIED_NONCOVERED', 'DENIED_MISSING_INFO', 'DENIED_MISSING_PRIMARY', 'DENIED_AUTH',
                               'DENIED_REFERRAL', 'DENIED_DUPLICATE_UNPAID', 'EMA_DENIED', 'EMA_DENIAL_AS_CONTRACTUAL'],
        'note': 'EMA_DENIAL_AS_CONTRACTUAL is a denial EMA wrote off; it is appealed like any denial while its window is open.'},
    'underpayment': {
        'label': "Underpayment against the payer's own rate or the contract",
        'remitwatch_signals': ['UNDERPAID_VS_OWN_RATE', 'DOWNCODE_RATE', 'MPPR_EXCESS'],
        'note': 'For Medicare and non-contracted MA the yardstick is the Medicare fee schedule amount, not a contract.'},
    'no_answer_clean_claim': {
        'label': 'No answer to a clean claim', 'remitwatch_signals': ['NO_ANSWER', 'EMA_OPEN_CLAIM'],
        'note': 'Start every clock from the payer\'s received date (277CA or claim status), not the practice\'s clearinghouse 999.'},
    'payment_reported_not_received': {
        'label': 'Payment reported (835) but not received', 'remitwatch_signals': ['unposted 835 money after the trace steps'],
        'note': 'Few rules speak to this directly. Where none does, the step is a written trace or inquiry, not an appeal.'},
    'corrected_claim_refused': {
        'label': 'Corrected claim refused (or duplicate denial of a corrected claim)',
        'remitwatch_signals': ['REJECTED_PRE_PAYER', 'DENIED_DUPLICATE_UNPAID'],
        'note': 'Medicare Part B takes no corrected claims: errors go to a clerical reopening or a new claim.'},
    'medical_necessity_denial': {
        'label': 'Medical-necessity or level-of-care denial',
        'remitwatch_signals': ['DENIED_MEDICAL_NECESSITY', 'DENIED_LEVEL_OF_SERVICE', 'DENIED_FREQUENCY', 'DOWNCODE_LEVEL_DENIED',
                               'DOWNCODE_LEVEL_REDUCED']},
    'bundling_modifier_denial': {
        'label': 'Bundling, modifier or coding denial, or a downcode (code changed or paid at a lower code)',
        'remitwatch_signals': ['DENIED_MISSING_MODIFIER', 'DENIED_CODING', 'DOWNCODE', 'DOWNCODE_LINE_SWAP', 'DOWNCODE_VS_BILLED',
                               'DOWNCODE_CHARGE', 'CODE_CHANGED']},
    'timely_filing_denial_with_proof': {
        'label': 'Timely-filing denial when the practice has proof of timely filing', 'remitwatch_signals': ['DENIED_TIMELY_FILING']},
    'recoupment_takeback': {'label': 'Recoupment or takeback', 'remitwatch_signals': ['TAKEBACK']},
    'credentialing_enrollment_denial': {'label': 'Credentialing or enrollment denial', 'remitwatch_signals': ['DENIED_PROVIDER']},
}

ANCHORS = {
    'claim_received': "Payer's received date for the claim (277CA, claim status or payer acknowledgment). Kentucky: the date it reached the insurer's published address; the practice's own clearinghouse time does not count (kentucky/KY-03).",
    'remit_date': 'Date printed on the remittance, EOB, EOP, PRA or denial notice.',
    'remit_received': 'Date the practice received the notice. Medicare and Medicare Advantage presume 5 calendar days after the notice date unless there is evidence to the contrary.',
    'request_received': "Date the payer received our request. For a fax: the date on the fax delivery record (pages accepted by the payer's machine). For a portal: the confirmation date. For mail: the delivery date.",
    'decision_date': "Date printed on the payer's decision.",
    'decision_received': 'Date the practice received the decision (presumed 5 days after the notice date where the rule says so).',
    'payment_date': "Payer's payment date (EFT effective date or check date).",
    'payment_received': 'Date the practice received payment (EFT posting or check receipt).',
    'recoupment_notice_date': 'Date on the overpayment or recoupment notice.',
    'date_of_service': 'Date of service (line From date for professional claims with spans).',
    'complaint_assigned': 'Date the regulator assigned or forwarded the complaint to the payer.',
}

DECISIONS = {
    'automatic': 'Runs by itself on its date: follow-ups of a request a person already sent, computed interest lines, claim status checks. Logged with its rule.',
    'person': 'A biller does it from the item panel (portal upload, mail, phone call, a patient signature).',
    'owner_per_kind': 'The owner switches this step on once for a payer kind (and can switch it off). After that it runs on its date like an automatic step and each send is logged. Used for regulator complaints, second levels, independent and external reviews, notices of dispute.',
    'owner_each_time': 'The owner decides each case: lawsuits, small claims, arbitration demands, ALJ hearings, congressional inquiries, anything that costs fees or names people.',
}

# ---------------------------------------------------------------- traditional Medicare
MFFS = 'medicare_ffs/'
MEDICARE_COMMON = {
    'label': 'Traditional Medicare Part B',
    'who': 'Claims paid by a Medicare Administrative Contractor (MAC). The practice is a party on assigned claims.',
    'applies_when': ['payer_kind is medicare (not Medicare Advantage, not a supplement)', 'claim billed on assignment (participating)'],
    'defaults': {
        'binding_rules': [MFFS + 'MFFS-01-redet-filing-deadline', MFFS + 'MFFS-02-redet-content-form-vs-letter',
                          MFFS + 'MFFS-04-redet-60-day-decision-no-escalation', MFFS + 'MFFS-05-redet-notice-content',
                          MFFS + 'MFFS-26-no-fee-for-appeals', MFFS + 'MFFS-27-party-standing-assigned-claims',
                          MFFS + 'V-310.1.B.4-no-duplicates', MFFS + 'V-310.1-NOTE-mail'],
        'denial_must_contain': [
            D('Remittance: the basis for the denial, the CARC and RARC codes, and whom to contact (42 CFR 405.921(b)(2)).', MFFS + 'MFFS-05-redet-notice-content'),
            D('Redetermination notice (MRN) when unfavorable: the items in 42 CFR 405.956(b)(1)-(10), including any specific missing documentation ((b)(6)) and how to get the specific policy, manual or regulation provision used ((b)(9)). A full reversal needs only the (c) statement.', MFFS + 'MFFS-05-redet-notice-content')],
        'may_request': [R('The specific policy, manual or regulation provision used, through the procedure the MRN must state (405.956(b)(9)).', MFFS + 'MFFS-05-redet-notice-content'),
                        R('A written answer to a status or calculation inquiry within 45 business days (CMS manual standard; no payment penalty).', MFFS + 'MFFS-24-written-and-congressional-inquiry-clocks', 'request')],
        'batch': {'accepted': 'limited',
                  'how': "CMS lets one paper redetermination cover several beneficiaries, but the MAC may split it (MCPM Ch. 29 sec. 300.5.B; researcher text, not separately quoted). Portals take one claim at a time; CGS forms take one claim per form. At the ALJ level claims for 'similar or related services' can be aggregated to reach $200, but each claim's own 60-day hearing deadline still applies.",
                  'rules': [MFFS + 'CH-CGS-J15B-redetermination-channels', MFFS + 'MFFS-11-alj-hearing-aic-aggregation']},
        'never_claim': [
            'Kentucky or Indiana prompt-pay law, interest rates or an insurance department complaint (a MAC is a federal contractor; legal inference, could not be checked as a quoted rule).',
            'Any consequence for a late redetermination: no interest, no deemed approval and no escalation at the MAC level.',
            'Interest on a claim that was denied and later paid on appeal.',
            'That the MAC must change the Physician Fee Schedule amount; only the payment applied to a line is appealable.',
            'A second redetermination for the same line while one is pending (duplicates are combined and timed from the first).',
            'That a faxed redetermination was filed at CGS (CGS does not accept faxed redeterminations).',
            'Recovery of the 2 percent sequestration cut (CARC 253); it is statutory through FY2032 and the first 5 months of FY2033.'],
    },
    'issues': {
        'denial_to_appeal': {
            'answer_by': [A('Decide the redetermination and send notice', 60, 'calendar', 'request_received', MFFS + 'MFFS-04-redet-60-day-decision-no-escalation',
                            'Plus up to 14 days for each later evidence submission (405.946(b)). A favorable decision is complete only when the adjustment has started.'),
                          A('Pay a favorable redetermination', 30, 'calendar', 'decision_date', MFFS + 'MFFS-07-effectuation-30-days')],
            'interest': {'owed': 'no', 'rules': [MFFS + 'MFFS-18-interest-exclusions'],
                         'how': 'No clean-claim interest on a claim first denied and paid after appeal.'},
            'filing_window': [A('File the redetermination', 120, 'calendar', 'remit_received', MFFS + 'MFFS-01-redet-filing-deadline',
                                'Receipt presumed remit_date + 5; filed when the MAC receives it; weekend and holiday deadlines roll to the next work day. A reopening request does not stop this clock. Resubmissions do not restart it.')],
            'next_escalation': [
                E('If the MAC is late: written status inquiry limited to status (no request for action, or it becomes a duplicate appeal).', 'day 61 after request_received (plus extensions)',
                  [MFFS + 'MFFS-24-written-and-congressional-inquiry-clocks', MFFS + 'V-310.1.B.4-no-duplicates'], 'automatic'),
                E('QIC reconsideration (C2C QIC Part B North for Kentucky and Indiana residents; Part B South for residents of southern states).',
                  'when an unfavorable MRN arrives; file within 180 days of its receipt',
                  [MFFS + 'MFFS-08-reconsideration-filing', MFFS + 'CH-QIC-PartB-and-OMHA', MFFS + 'MFFS-09-full-and-early-presentation'], 'owner_per_kind',
                  'Put every piece of evidence in at the QIC: evidence first offered later is excluded without good cause.'),
                E('Dismissed request: cure and ask the MAC to vacate (180 days), or QIC review of the dismissal (60 days).', 'on a dismissal notice',
                  MFFS + 'MFFS-06-redet-dismissal-vacate-or-qic-review', 'automatic')],
            'gaps': ['No rule requires a MAC to disclose who reviewed a redetermination; do not demand reviewer credentials from a MAC as a right.']},
        'underpayment': {
            'binding_rules': [MFFS + 'MFFS-22-what-is-not-appealable', MFFS + 'MFFS-13-reopening-party-request-clerical', MFFS + 'MFFS-14-reopening-good-cause-4-years',
                              MFFS + 'MFFS-28-redetermination-scope-risk', MFFS + 'DNA-sequestration-years'],
            'answer_by': [A('Complete an accepted reopening', 60, 'calendar', 'request_received', MFFS + 'MFFS-13-reopening-party-request-clerical',
                            "Not for 'big box' requests (40 or more beneficiaries or claims and $40,000 or more)."),
                          A('Decide a redetermination of the payment amount', 60, 'calendar', 'request_received', MFFS + 'MFFS-04-redet-60-day-decision-no-escalation')],
            'interest': {'owed': 'unconfirmed', 'rules': [MFFS + 'MFFS-20-underpayment-interest-405-378'],
                         'how': '42 CFR 405.378 interest accrues only on a written MAC determination that an underpayment exists that is not paid within 30 days. Whether a favorable redetermination counts was not confirmed: do not claim it.'},
            'filing_window': [A('Request a reopening for any reason', 365, 'calendar', 'remit_date', MFFS + 'MFFS-13-reopening-party-request-clerical',
                                '1 year from the initial determination; 4 years for good cause such as an error on the face of the record; any time for a clerical error on an unfavorable determination (the MAC may dismiss if it disagrees, and that is not appealable).')],
            'next_escalation': [E('Clerical reopening (misapplied fee schedule, units, modifier, place of service, date of service) through myCGS or the WPS portal.', 'day 0',
                                  MFFS + 'MFFS-13-reopening-party-request-clerical', 'person'),
                                E('Redetermination of the payment amount when it needs review, or when the 120 days are about to close.', 'before day 120 after remit_received',
                                  [MFFS + 'MFFS-24-written-and-congressional-inquiry-clocks', MFFS + 'MFFS-28-redetermination-scope-risk'], 'person',
                                  'A letter that disputes the amount and says more is owed is an appeal of the payment amount. It opens the whole line to review, including medical necessity.')],
            'never_claim': ['That the fee schedule rate itself is wrong.', 'That CARC 253 sequestration is an underpayment.']},
        'no_answer_clean_claim': {
            'binding_rules': [MFFS + 'MFFS-17-clean-claim-30-day-interest', MFFS + 'MFFS-19-payment-floor', MFFS + 'MFFS-30-initial-determination-45-days-nonclean',
                              MFFS + 'MFFS-23-timely-filing-of-claims', MFFS + 'MFFS-24-written-and-congressional-inquiry-clocks'],
            'answer_by': [A('Pay a clean claim', 30, 'calendar', 'claim_received', MFFS + 'MFFS-17-clean-claim-30-day-interest',
                            'Counting starts the day after receipt. Electronic claims may not be paid within 13 days of receipt (paper 28).'),
                          A('Decide a claim that is not clean', 45, 'calendar', 'claim_received', MFFS + 'MFFS-30-initial-determination-45-days-nonclean',
                            'The count stops while a development letter is out and for claims held for medical review; no consequence is stated.')],
            'interest': {'owed': 'yes_automatic', 'formula': 'medicare_prompt_pay', 'rules': [MFFS + 'MFFS-17-clean-claim-30-day-interest'],
                         'how': 'The MAC adds it and reports it on the remittance. There is no form to claim it; a missing amount is raised by written inquiry.'},
            'filing_window': [A('Refile an unanswered or rejected claim', 365, 'calendar', 'date_of_service', MFFS + 'MFFS-23-timely-filing-of-claims',
                                'A rejected or unprocessable claim was never received. Leap day: a February 29 service must be filed by February 28 of the next year.')],
            'next_escalation': [E('Claim status check (276/277 through TriZetto where the account allows).', 'day 31 after claim_received', [MFFS + 'MFFS-17-clean-claim-30-day-interest'], 'automatic'),
                                E('Written inquiry: status and any missing interest.', 'day 31 (clean) or day 46 (not clean) after claim_received',
                                  MFFS + 'MFFS-24-written-and-congressional-inquiry-clocks', 'automatic'),
                                E('Feedback to CMS about the MAC (CGS lists a CMS address; no PHI).', 'pattern of missed clocks', [MFFS + 'CH-CGS-contacts-and-CMS-feedback', MFFS + 'MFFS-25-mac-performance-accountability'], 'owner_per_kind')]},
        'payment_reported_not_received': {
            'binding_rules': [MFFS + 'MFFS-24-written-and-congressional-inquiry-clocks'],
            'answer_by': [A('Answer a written inquiry', 45, 'business', 'request_received', MFFS + 'MFFS-24-written-and-congressional-inquiry-clocks')],
            'interest': {'owed': 'not_established', 'rules': [MFFS + 'MFFS-17-clean-claim-30-day-interest'],
                         'how': "The statute runs interest to 'the date on which payment is made'. No rule was found on a payment the remittance reports but the practice never received; do not claim interest for it."},
            'next_escalation': [E('Written inquiry asking the MAC to trace the payment.', 'after the practice\'s own deposit and EMA trace finds nothing', MFFS + 'MFFS-24-written-and-congressional-inquiry-clocks', 'automatic')],
            'gaps': ['No Medicare rule on an EFT reported but not received was found in this research.']},
        'corrected_claim_refused': {
            'binding_rules': [MFFS + 'MFFS-13-reopening-party-request-clerical', MFFS + 'MFFS-29-resubmission-does-not-restart-clock',
                              MFFS + 'MFFS-15-no-reopening-while-appeal-pending', MFFS + 'MFFS-23-timely-filing-of-claims'],
            'answer_by': [A('Complete an accepted reopening', 60, 'calendar', 'request_received', MFFS + 'MFFS-13-reopening-party-request-clerical')],
            'interest': {'owed': 'no', 'rules': [], 'how': 'Not applicable.'},
            'next_escalation': [E('Clerical reopening, not a corrected claim (Medicare Part B takes no corrected claims).', 'day 0', MFFS + 'MFFS-13-reopening-party-request-clerical', 'person'),
                                E('A returned (unprocessable) claim is fixed and sent as a new claim inside timely filing; it has no appeal rights.', 'day 0', MFFS + 'MFFS-23-timely-filing-of-claims', 'person'),
                                E('If the reopening is refused and the 120 days are still open, file a redetermination (a timely redetermination routed to reopening is processed as a redetermination if it cannot be reopened).',
                                  'before day 120 after remit_received', MFFS + 'MFFS-13-reopening-party-request-clerical', 'person')],
            'never_claim': ['That a resubmission restarts the appeal clock.', 'That a refusal to reopen can be appealed.']},
        'medical_necessity_denial': {
            'binding_rules': [MFFS + 'MFFS-03-redet-evidence-and-clock-extension', MFFS + 'MFFS-09-full-and-early-presentation', MFFS + 'MFFS-16-adr-no-response-denials',
                              MFFS + 'SUMMARY-policy-validity-belongs-at-QIC-ALJ', MFFS + 'MFFS-28-redetermination-scope-risk'],
            'answer_by': [A('Decide the redetermination', 60, 'calendar', 'request_received', MFFS + 'MFFS-04-redet-60-day-decision-no-escalation'),
                          A('Decide the QIC reconsideration (then escalation to the ALJ is open)', 60, 'calendar', 'request_received', MFFS + 'MFFS-10-qic-60-days-and-escalation')],
            'interest': {'owed': 'no', 'rules': [MFFS + 'MFFS-18-interest-exclusions'], 'how': 'No interest on denials paid after appeal.'},
            'next_escalation': [E('Records denial after an unanswered ADR (CO-50 with M127): send the records with a redetermination; it is processed as a reopening.', 'day 0',
                                  MFFS + 'MFFS-16-adr-no-response-denials', 'person'),
                                E('QIC reconsideration with every piece of evidence.', 'on an unfavorable MRN', [MFFS + 'MFFS-08-reconsideration-filing', MFFS + 'MFFS-09-full-and-early-presentation'], 'owner_per_kind'),
                                E('ALJ hearing ($200 in controversy in 2026 and 2027; aggregation allowed, each claim on its own deadline; copies to all parties).', 'within 60 days of receipt of the QIC decision, or on QIC delay',
                                  [MFFS + 'MFFS-11-alj-hearing-aic-aggregation', MFFS + 'MFFS-10-qic-60-days-and-escalation'], 'owner_each_time')],
            'never_claim': ['That an LCD is invalid. Argue that the LCD or manual does not fit this claim; the QIC and ALJ may decline to follow a policy for one claim but may not set aside an LCD.']},
        'bundling_modifier_denial': {
            'binding_rules': [MFFS + 'MFFS-13-reopening-party-request-clerical', MFFS + 'MFFS-22-what-is-not-appealable'],
            'answer_by': [A('Complete an accepted reopening', 60, 'calendar', 'request_received', MFFS + 'MFFS-13-reopening-party-request-clerical'),
                          A('Decide a redetermination', 60, 'calendar', 'request_received', MFFS + 'MFFS-04-redet-60-day-decision-no-escalation')],
            'interest': {'owed': 'no', 'rules': [MFFS + 'MFFS-18-interest-exclusions'], 'how': 'None on denials paid after appeal.'},
            'next_escalation': [E('Modifier or data error: clerical reopening (portal, within 1 year).', 'day 0', MFFS + 'MFFS-13-reopening-party-request-clerical', 'person'),
                                E('Coding dispute that needs review: redetermination within 120 days.', 'day 0', MFFS + 'MFFS-01-redet-filing-deadline', 'person'),
                                E('Duplicate denial: only appealable when disputing that it was a duplicate; duplicate denials are clerical reopenings.', 'day 0', MFFS + 'MFFS-22-what-is-not-appealable', 'person')]},
        'timely_filing_denial_with_proof': {
            'binding_rules': [MFFS + 'MFFS-23-timely-filing-of-claims'],
            'answer_by': [],
            'interest': {'owed': 'no', 'rules': [], 'how': 'Not applicable.'},
            'next_escalation': [E('Ask the MAC for an informal review with proof the MAC itself received the claim in time (MAC acceptance, not the clearinghouse 999).', 'day 0',
                                  MFFS + 'MFFS-23-timely-filing-of-claims', 'person', 'A timely-filing denial has no formal appeal. Exceptions: MAC or CMS error, retroactive entitlement, Medicaid or MA retroactive recoupment.')],
            'never_claim': ['A right to appeal a timely-filing denial.']},
        'recoupment_takeback': {
            'binding_rules': [MFFS + 'MFFS-21-recoupment-stop-and-interest-on-reversal', MFFS + 'V-405.374-rebuttal', MFFS + 'MFFS-07-effectuation-30-days',
                              'tactics_and_risk/TR-65', 'tactics_and_risk/TR-24'],
            'answer_by': [A('Consider a rebuttal statement', 15, 'calendar', 'request_received', MFFS + 'V-405.374-rebuttal', 'The rebuttal does not delay recoupment and is not appealable.'),
                          A('Decide the redetermination of the overpayment', 60, 'calendar', 'request_received', MFFS + 'MFFS-04-redet-60-day-decision-no-escalation')],
            'interest': {'owed': 'yes_on_reversal', 'rules': [MFFS + 'MFFS-21-recoupment-stop-and-interest-on-reversal', MFFS + 'MFFS-07-effectuation-30-days'],
                         'how': 'If the overpayment is reversed at the ALJ level or above, recouped money earns interest for full 30-day periods; a reversed overpayment not refunded on time earns 405.378 interest.'},
            'filing_window': [A('File the redetermination before recoupment can start', 40, 'calendar', 'recoupment_notice_date', MFFS + 'MFFS-21-recoupment-stop-and-interest-on-reversal',
                                'Recoupment may begin no earlier than day 41; a timely valid request stops it. Keep it stopped with a reconsideration within 60 days of the redetermination notice date.'),
                              A('Rebuttal statement (optional, does not stop recoupment)', 15, 'calendar', 'recoupment_notice_date', MFFS + 'V-405.374-rebuttal')],
            'next_escalation': [E('Redetermination of the overpayment (WPS portal has a flag that pauses collection).', 'day 0, before day 41',
                                  [MFFS + 'MFFS-21-recoupment-stop-and-interest-on-reversal', MFFS + 'CH-WPS-J8-fax-and-portal'], 'person'),
                                E('QIC reconsideration keeps recoupment stopped until decided.', 'within 60 days of the redetermination notice date', ['tactics_and_risk/TR-65', MFFS + 'MFFS-21-recoupment-stop-and-interest-on-reversal'], 'owner_per_kind')],
            'note': 'A MAC may reopen its own determinations within 1 year for any reason, 4 years for good cause, and at any time for fraud or similar fault.'},
        'credentialing_enrollment_denial': {
            'binding_rules': [], 'answer_by': [], 'interest': {'owed': 'not_researched', 'rules': [], 'how': ''},
            'next_escalation': [], 'gaps': ['Medicare enrollment rules were not part of this research. Use no citation.']},
    },
}

MEDICARE_CHANNELS = {
    'medicare_ffs_cgs_j15_ky': {
        'label': 'Traditional Medicare, CGS J15 (Kentucky)',
        'channels': {
            'redetermination': {'fax': 'none: "Redeterminations do not accept faxed request."', 'portal': 'myCGS (preferred; one claim per request; up to 10 PDFs)',
                                'mail': 'CGS, Attn: J15 Part B Appeals Department, PO Box 20018, Nashville, TN 37202', 'rule': MFFS + 'CH-CGS-J15B-redetermination-channels',
                                'other': 'esMD is listed for J15 appeals on a CGS notice from its HHH site; confirm Part B intake before use (' + MFFS + 'V-esMD).'},
            'reopening': {'portal': 'myCGS reopenings', 'mail': 'CGS, Attn: Reopenings, PO Box 20018 (one claim per form)', 'fax': 'none listed'},
            'inquiry': {'fax': 'General Correspondence 615.782.4490', 'mail': 'PO Box 20018', 'rule': MFFS + 'CH-CGS-contacts-and-CMS-feedback'},
            'overpayment_offset': {'fax': 'Kentucky 615.664.5916 (offset requests and OPR forms)', 'rule': MFFS + 'MFFS-21-recoupment-stop-and-interest-on-reversal'},
            'qic': {'fax': '904-539-4081 (Part B North)', 'portal': 'C2C Part B North QIC Appeals Portal', 'mail': 'C2C Innovative Solutions, QIC Part B North, PO Box 45208, Jacksonville, FL 32232-5208',
                    'rule': MFFS + 'CH-QIC-PartB-and-OMHA'},
            'cms_feedback': {'email': 'OPOLE_IFM_CHIKCGP@cms.hhs.gov (no PHI)', 'rule': MFFS + 'CH-CGS-contacts-and-CMS-feedback'}},
        'route_switch_note': 'A redetermination never goes by fax at CGS. Portal first, mail second. A saved fax number must not be used for level 1 (integration item I1).'},
    'medicare_ffs_wps_j8_in': {
        'label': 'Traditional Medicare, WPS J8 (Indiana)',
        'channels': {
            'redetermination': {'fax': 'CONFLICT: (608) 224-3502 on the redetermination form, (608) 224-3504 on the overpayment form. Do not automate until WPS confirms.',
                                'portal': 'WPS portal (per claim; documents only at submission; up to 20 files)', 'mail': 'WPS Government Services, ATTN: Redeterminations, PO Box 8580, Madison, WI 53708-8580',
                                'rule': MFFS + 'CH-WPS-J8-fax-and-portal'},
            'reopening': {'portal': 'WPS clerical error reopening (a claim can be reopened once through the portal)', 'rule': MFFS + 'CH-WPS-J8-fax-and-portal'},
            'qic': {'fax': '904-539-4081 (Part B North)', 'portal': 'C2C Part B North QIC Appeals Portal', 'mail': 'PO Box 45208, Jacksonville, FL 32232-5208', 'rule': MFFS + 'CH-QIC-PartB-and-OMHA'}},
        'route_switch_note': 'Use the portal until WPS confirms which Indiana fax number is right.'},
}

# ---------------------------------------------------------------- Medicare Advantage
MA = 'medicare_advantage/'
MA_NONCONTRACTED = {
    'label': 'Medicare Advantage, practice NOT contracted with that MA organization for that product',
    'who': 'Humana MA, Anthem MA, UHC MA, Wellcare and others, where the practice has no written agreement for the service.',
    'applies_when': ['payer_kind is medicare_advantage', 'the owner has marked this MA organization and product as not contracted'],
    'defaults': {
        'binding_rules': [MA + 'MA-01', MA + 'MA-04', MA + 'MA-05', MA + 'MA-06', MA + 'MA-08', MA + 'MA-09', MA + 'MA-12', MA + 'MA-16',
                          MA + 'V-guidance-10.5-receipt', MA + 'MA-26', MA + 'V-plan-answers-for-vendors'],
        'denial_must_contain': [
            D('The specific reasons for the denial and the right to reconsideration (42 CFR 422.568(e)).', MA + 'MA-16'),
            D('For a non-contract provider: the specific reason, a description of the appeals process, the Waiver of Liability requirement with the form or a link, and that the provider has 65 calendar days from the remittance date to request reconsideration (guidance 40.12.1). The IDN form may not be used for non-contract providers.', MA + 'MA-16')],
        'may_request': [
            R('The specific reason for the denial.', MA + 'MA-16'),
            R('The Medicare rule (NCD, LCD of the local MAC, statute) or the publicly posted internal criterion with its evidence summary that the decision relied on.', MA + 'MA-18'),
            R('Which physician or other professional reviewed a medical-necessity denial before it issued, and in what field (422.566(d) requires such review; disclosure of the name is a request, not a stated right).', MA + 'MA-11', 'request'),
            R('That a medical-necessity reconsideration be decided by a physician with expertise in the field, not involved in the first decision.', MA + 'MA-10')],
        'batch': {'accepted': 'no_rule',
                  'how': 'No regulation or CMS guidance addresses one reconsideration covering many claims. Send one cover letter with a per-claim schedule; each claim carries its own Waiver of Liability. At the ALJ level, claims for similar or related services can be aggregated to reach $200.',
                  'rules': [MA + 'MA-14']},
        'channels_note': 'Humana MA non-par appeals: Humana Grievance and Appeal Department, P.O. Box 14165, Lexington, KY 40512-4165, or Availity. Anthem MA non-par: Medicare Complaints, Appeals and Grievances, Attention: Non-Contracted Provider Appeals, Mailstop OH0205-A537, 4361 Irwin Simpson Rd, Mason, OH 45040. UHC non-contracted: UHC Provider Portal per its sheet. Part C IRE dismissal reviews: fax (904) 539-4080. Use only numbers the CHANNELS directory marks verified.',
        'never_claim': [
            'Kentucky or Indiana prompt-pay law, state interest rates or a state insurance department complaint (federal MA standards supersede state law other than licensing and solvency).',
            'Interest on a denial overturned on appeal (no MA source applies or rejects the Original Medicare exclusion; unconfirmed).',
            'That the old CMS payment dispute contractor exists (it ended January 31, 2014).',
            'CMS sanctions or civil money penalties as something the practice will impose; they are CMS remedies. State only that substantial failure is a ground for CMS action.',
            'That a billing company signed the Waiver of Liability (the provider must sign it).'],
    },
    'issues': {
        'denial_to_appeal': {
            'answer_by': [A('Decide the payment reconsideration and pay if reversed', 60, 'calendar', 'request_received', MA + 'MA-09',
                            "From receipt of the request, or from receipt of the Waiver of Liability if it came later. 'Day one' is the day after receipt; a fax counts as received when the transmission report shows it was successfully transmitted."),
                          A('Forward the file to the CMS IRE if upheld in any part or not decided', 60, 'calendar', 'request_received', MA + 'MA-09'),
                          A('IRE decides a payment request', 60, 'calendar', 'IRE receipt of the file', MA + 'MA-13'),
                          A('Pay after an IRE reversal', 30, 'calendar', 'plan receipt of the IRE notice', MA + 'MA-09')],
            'filing_window': [A('File the reconsideration with a signed Waiver of Liability', 65, 'calendar', 'remit_date', MA + 'MA-08',
                                '60 days after receipt, receipt presumed 5 days after the notice date. Filed when the plan receives it. Late filing allowed for good cause stated in writing (for example, no notice or a notice without the required appeal information).')],
            'interest': {'owed': 'unconfirmed', 'rules': [MA + 'MA-02'], 'how': 'Do not claim interest on an overturned denial.'},
            'next_escalation': [
                E('Notice that the plan\'s 60 days have run and the file is now due at the IRE; check the C2C Part C status lookup.', 'day 61 after request_received',
                  [MA + 'MA-09', MA + 'MA-13'], 'automatic'),
                E('IRE review of a dismissal (the dismissal reasons in 422.582(f) are the only ones allowed); also ask the plan to vacate.', 'on a dismissal; file within 60 days of the dismissal notice',
                  MA + 'MA-12', 'owner_per_kind', 'Two CMS documents say 65 days; the regulation says 60. File by day 60.'),
                E('Non-effectuation letter after an IRE reversal, copied to the IRE.', 'day 31 after the IRE reversal', MA + 'MA-09', 'owner_per_kind'),
                E('ALJ hearing ($200 in controversy; similar or related claims can be aggregated; no Part C decision deadline at this level).', 'within 60 days of receipt of the IRE decision',
                  MA + 'MA-14', 'owner_each_time'),
                E('CMS provider complaint (Complaints Tracking Module) for a pattern: the plan must attempt contact within 7 days and resolve within 30 days of assignment.', 'after the plan\'s own process, for patterns',
                  [MA + 'MA-24', MA + 'MA-25'], 'owner_per_kind')]},
        'underpayment': {
            'binding_rules': [MA + 'MA-22', MA + 'MA-06', MA + 'MA-23', MA + 'MA-31', MA + 'MA-30', MA + 'MA-33', MA + 'V-network-gaps'],
            'answer_by': [A('Decide a reconsideration of a partial payment, downcode, bundling or rate-of-payment decision', 60, 'calendar', 'request_received', MA + 'MA-09'),
                          A("UHC MA: answer a non-contracted payment dispute (UHC's own sheet)", 30, 'calendar', 'request_received', MA + 'MA-31')],
            'filing_window': [A('Reconsideration with Waiver of Liability', 65, 'calendar', 'remit_date', MA + 'MA-08'),
                              A('UHC MA non-contracted payment dispute', 120, 'calendar', 'payment_date', MA + 'MA-31')],
            'interest': {'owed': 'no', 'rules': [], 'how': 'Interest attaches to late payment of clean claims, not to the amount.'},
            'yardstick': 'Price each line at the lesser of the billed charge or the Original Medicare amount for the locality (95 percent for a non-participating physician), net of the plan\'s cost sharing. The "no less than" words are CMS guidance; the regulations frame it as a safe harbor and a cap.',
            'next_escalation': [E('Reconsideration with Waiver of Liability (partial payments, downcoding, bundling and rate of payment are appealable without a zero payment).', 'day 0', MA + 'MA-06', 'automatic'),
                                E('CMS complaint for patterns after the plan\'s process (CMS account managers monitor non-contract payment disputes).', 'after the plan process', [MA + 'MA-23', MA + 'MA-25'], 'owner_per_kind')],
            'never_claim': ["That Humana's or Anthem's narrower non-par appeal lists bind the practice; quote CMS guidance 50.1.1 back.",
                            'Wellcare\'s "100% of the Medicare rate" without showing the authorization (its manual requires authorization for non-par services except listed ones).']},
        'no_answer_clean_claim': {
            'binding_rules': [MA + 'MA-01', MA + 'MA-02', MA + 'MA-05'],
            'answer_by': [A('Pay a clean claim (per-claim interest after day 30)', 30, 'calendar', 'claim_received', MA + 'MA-01'),
                          A('Pay or deny any other non-contracted claim', 60, 'calendar', 'claim_received', MA + 'MA-01')],
            'interest': {'owed': 'yes', 'formula': 'medicare_prompt_pay', 'rules': [MA + 'MA-02'],
                         'how': 'Clean claims paid after day 30: payment x Treasury rate on the payment date x days late / 365 (366 in a leap year), on the net payment, rounded to the cent.'},
            'next_escalation': [E('Demand payment with interest; state that silence past the deadline is an adverse organization determination that the practice is appealing, and attach the reconsideration with a signed Waiver of Liability.',
                                  'day 61 after claim_received (day 31 for a clean claim demand)', [MA + 'MA-05', MA + 'MA-06', MA + 'MA-09'], 'automatic')],
            'gaps': ['When a plan never issues any notice, the date the 65-day reconsideration window runs from is not addressed; file at day 61 and let the plan\'s 60-day clock run from its receipt.']},
        'payment_reported_not_received': {
            'binding_rules': [MA + 'MA-02'], 'answer_by': [],
            'interest': {'owed': 'not_established', 'rules': [MA + 'MA-02'], 'how': 'No rule found on a reported payment never received.'},
            'next_escalation': [E('Written trace request to the plan.', 'after the practice\'s own trace', MA + 'MA-02', 'automatic')],
            'gaps': ['No MA rule addresses an EFT reported but not received.']},
        'corrected_claim_refused': {
            'binding_rules': [MA + 'MA-15'],
            'answer_by': [A('Complete a reopening (CMS "should")', 60, 'calendar', 'request_received', MA + 'MA-15')],
            'interest': {'owed': 'no', 'rules': [], 'how': ''},
            'filing_window': [A('Clerical-error reopening on an unfavorable determination', None, 'any time', 'remit_date', MA + 'MA-15', 'If the plan disagrees it is clerical it must dismiss and give appeal rights if the appeal window is still open.')],
            'next_escalation': [E('Clerical-error reopening request (math, data entry or coding, computer errors, duplicate denials).', 'day 0', MA + 'MA-15', 'automatic'),
                                E('If dismissed and still inside 65 days: reconsideration with Waiver of Liability.', 'on dismissal', MA + 'MA-08', 'automatic')]},
        'medical_necessity_denial': {
            'binding_rules': [MA + 'MA-11', MA + 'MA-18', MA + 'MA-10', MA + 'MA-19', MA + 'MA-20', MA + 'MA-17', 'tactics_and_risk/TR-04',
                              MA + 'V-new-evidence-allowed', MA + 'V-outreach-records'],
            'answer_by': [A('Decide the reconsideration', 60, 'calendar', 'request_received', MA + 'MA-09'),
                          A('Prior authorization decision for a PA item (standard)', 7, 'calendar', 'request_received', MA + 'MA-17', '72 hours expedited. A missed deadline is itself an adverse determination.')],
            'interest': {'owed': 'no', 'rules': [], 'how': ''},
            'may_request': [R('Confirmation that an algorithm did not decide on a larger data set instead of this patient\'s record, and which records the reviewer read.', MA + 'MA-18', 'request'),
                            R("The plan's posted prior authorization statistics (approval after appeal; decision times).", MA + 'V-422.122c-pa-stats', 'request')],
            'next_escalation': [E('Reconsideration citing the LCD of the local MAC (CGS for Kentucky, WPS for Indiana) and the record; if a PA approval exists, 422.138(c) bars a later medical-necessity denial.',
                                  'day 0', [MA + 'MA-18', MA + 'MA-19'], 'automatic'),
                                E('IRE (automatic by law when upheld); new evidence may still be added in MA appeals.', 'day 61', [MA + 'MA-09', MA + 'V-new-evidence-allowed'], 'automatic')],
            'note': 'A records-missing denial issued with no outreach request is a procedural defect to cite (the plan need not reach out if it believes it has everything).'},
        'bundling_modifier_denial': {
            'binding_rules': [MA + 'MA-06', MA + 'MA-15', MA + 'MA-22'],
            'answer_by': [A('Decide the reconsideration', 60, 'calendar', 'request_received', MA + 'MA-09')],
            'interest': {'owed': 'no', 'rules': [], 'how': ''},
            'next_escalation': [E('Clerical coding error: reopening. Bundling or a lower level paid: reconsideration with Waiver of Liability (CMS lists bundling, disputed rate of payment and level of care among appealable issues).',
                                  'day 0', [MA + 'MA-15', MA + 'MA-06'], 'automatic')]},
        'timely_filing_denial_with_proof': {
            'binding_rules': [MA + 'MA-05', MA + 'MA-06'], 'answer_by': [A('Decide the reconsideration', 60, 'calendar', 'request_received', MA + 'MA-09')],
            'interest': {'owed': 'no', 'rules': [], 'how': ''},
            'next_escalation': [E('Reconsideration with Waiver of Liability and proof of the plan\'s receipt date.', 'day 0', MA + 'MA-06', 'automatic')],
            'gaps': ['No MA timely-filing rule was researched; argue from the plan\'s received date and the 422.520 clocks.']},
        'recoupment_takeback': {
            'binding_rules': [MA + 'V-plan-reopening-limits', MA + 'MA-19', MA + 'MA-15', 'tactics_and_risk/TR-24'],
            'answer_by': [A('Decide the reconsideration of the revised determination', 60, 'calendar', 'request_received', MA + 'MA-09')],
            'interest': {'owed': 'no', 'rules': [], 'how': ''},
            'next_escalation': [E('Reconsideration of the revised (adverse) determination with Waiver of Liability; demand the rationale and basis for reopening. A reopening after 1 year needs good cause (new and material evidence or obvious error) or fraud or similar fault; an authorized service cannot later be denied for medical necessity.',
                                  'day 0, within 65 days of the revised notice', [MA + 'V-plan-reopening-limits', MA + 'MA-19', MA + 'MA-08'], 'automatic')]},
        'credentialing_enrollment_denial': {'binding_rules': [], 'answer_by': [], 'interest': {'owed': 'not_applicable', 'rules': [], 'how': ''},
                                            'next_escalation': [], 'gaps': ['Not applicable to a non-contracted provider.']},
    },
}

MA_CONTRACTED = {
    'label': 'Medicare Advantage, practice contracted with that MA organization for that product',
    'who': 'Contracted (participating) MA claims where the member owes nothing more: the contract and the plan manual govern payment disputes.',
    'applies_when': ['payer_kind is medicare_advantage', 'the owner has marked this MA organization and product as contracted'],
    'defaults': {
        'binding_rules': [MA + 'MA-07', MA + 'MA-03', MA + 'MA-04', MA + 'MA-11', MA + 'MA-18', MA + 'MA-21', MA + 'V-422.202-notice-termination',
                          MA + 'V-plan-answers-for-vendors', MA + 'MA-24', MA + 'MA-25'],
        'denial_must_contain': [D('Anthem MA upheld reconsideration letters: support for the action, including statutes, regulations, policies, claims, codes or manual references (its own guidebook).', MA + 'MA-32'),
                                D('Prior authorization denials: a specific reason, whatever the channel (from January 1, 2026).', MA + 'MA-17')],
        'may_request': [R('The contract term that supports any reduction (the plan must pay under the contract).', MA + 'MA-03', 'request'),
                        R('The practice or UM guideline behind a recurring edit (guidelines must be communicated to providers).', MA + 'MA-21'),
                        R('Which physician or professional reviewed a medical-necessity reduction before it issued, and the field.', MA + 'MA-11', 'request'),
                        R('Written notice of a material change in participation rules applied without prior notice.', MA + 'V-422.202-notice-termination')],
        'batch': {'accepted': 'payer_specific',
                  'how': 'UHC: Claims Research Project for 20 or more paid or denied claims with the same administrative issue and no attachments. Anthem MA: one dispute may carry a listing of disputed claims. Humana: no bulk route found; repeat disputes on one encounter need new information.',
                  'rules': [MA + 'MA-31', MA + 'MA-32', MA + 'MA-30']},
        'never_claim': ['IRE, ALJ or Star Ratings appeal-measure consequences for a contracted payment dispute (422.562(c)(2) removes it from Subpart M).',
                        'State prompt-pay law or a state insurance department complaint.',
                        'A legal right to see the UM committee\'s records (they go to CMS on request, not to providers).'],
    },
    'issues': {
        'denial_to_appeal': {
            'answer_by': [A("Anthem MA: 'every effort' to resolve a claims payment reconsideration", 45, 'calendar', 'request_received', MA + 'MA-32'),
                          A("Anthem MA: 'every effort' to resolve a claim payment appeal", 30, 'calendar', 'request_received', MA + 'MA-32'),
                          A("Humana escalation: response 30 to 45 days from the acknowledgment (service target, no consequence)", 45, 'calendar', 'acknowledgment date', 'payer_manuals/HUM-2'),
                          A('Wellcare participating provider appeal review (after complete documentation)', 60, 'calendar', 'request_received', MA + 'MA-33')],
            'filing_window': [A('Humana dispute (all products)', 548, 'calendar', 'remit_received', MA + 'MA-30', '18 months from receipt of the original determination.'),
                              A('Anthem MA reconsideration', 120, 'calendar', 'remit_date', MA + 'MA-32'),
                              A('Anthem MA appeal after the reconsideration', 30, 'calendar', 'decision_date', MA + 'MA-32'),
                              A('UHC reconsideration and appeal together', 365, 'calendar', 'remit_date', MA + 'MA-31', '12 months total for both steps.'),
                              A('Wellcare participating provider appeal', 90, 'calendar', 'remit_date', MA + 'MA-33')],
            'interest': {'owed': 'contract', 'rules': [MA + 'MA-03'], 'how': "Only under the contract's prompt-payment clause."},
            'next_escalation': [E('Second level under the manual (Anthem appeal; Humana escalation email for participating providers).', 'on the first decision', [MA + 'MA-32', 'payer_manuals/HUM-2'], 'owner_per_kind'),
                                E('UHC Notice of Dispute (else waived in any forum).', 'within 60 days of the UHC appeal decision', MA + 'MA-31', 'owner_per_kind'),
                                E('Arbitration under the agreement (Humana template: AAA, fee refund to a winning physician in a group of fewer than six).', 'after internal review is exhausted', 'payer_manuals/HUM-4', 'owner_each_time'),
                                E('CMS provider complaint for patterns (CMS says it is not a party to contract matters; expect pure rate disputes to be declined).', 'after the plan process', [MA + 'MA-25', MA + 'MA-24'], 'owner_per_kind')]},
        'underpayment': {
            'binding_rules': [MA + 'MA-03', MA + 'MA-31', MA + 'MA-32', MA + 'MA-30', 'erisa_commercial/E27_payer_rate_files_transparency'],
            'answer_by': [A("Anthem MA: 'every effort' reconsideration", 45, 'calendar', 'request_received', MA + 'MA-32')],
            'interest': {'owed': 'contract', 'rules': [MA + 'MA-03'], 'how': 'Contract only.'},
            'next_escalation': [E('Payment dispute with the expected-versus-paid math on each line (Humana asks for the proposed correct payment and the difference).', 'day 0', MA + 'MA-30', 'automatic'),
                                E('UHC: Claims Research Project for 20 or more same-issue claims.', 'day 0', MA + 'MA-31', 'person')]},
        'no_answer_clean_claim': {
            'binding_rules': [MA + 'MA-03'], 'answer_by': [],
            'interest': {'owed': 'contract', 'rules': [MA + 'MA-03'], 'how': "Contract's prompt-payment clause (every MA provider contract must have one)."},
            'next_escalation': [E("Demand under the contract's prompt-payment clause, quoting it by section.", 'per contract', MA + 'MA-03', 'automatic')],
            'gaps': ['No federal day count governs a contracted claim. The practice must load each contract\'s prompt-pay clause.']},
        'payment_reported_not_received': {'binding_rules': [MA + 'MA-03'], 'answer_by': [], 'interest': {'owed': 'contract', 'rules': [], 'how': ''},
                                          'next_escalation': [E('Trace request to the plan.', 'after own trace', MA + 'MA-03', 'automatic')], 'gaps': ['No rule found.']},
        'corrected_claim_refused': {'binding_rules': [MA + 'MA-31'], 'answer_by': [], 'interest': {'owed': 'contract', 'rules': [], 'how': ''},
                                    'next_escalation': [E('Reconsideration through the plan\'s channel (UHC network providers must use digital tools).', 'day 0', 'payer_manuals/UHC-2', 'person')]},
        'medical_necessity_denial': {
            'binding_rules': [MA + 'MA-11', MA + 'MA-18', MA + 'MA-19', MA + 'MA-20', MA + 'MA-17', 'tactics_and_risk/TR-04'],
            'answer_by': [A('Prior authorization decision (standard, PA items)', 7, 'calendar', 'request_received', MA + 'MA-17')],
            'interest': {'owed': 'contract', 'rules': [], 'how': ''},
            'next_escalation': [E('Pre-service: physician reconsideration; upheld pre-service reconsiderations go to the IRE even for contracted physicians.', 'on a PA denial or a missed PA deadline', [MA + 'MA-17', MA + 'MA-07'], 'person'),
                                E('Post-service: contract dispute citing 422.566(d), 422.101(c) and 422.138(c); a CMS complaint if refused.', 'day 0', [MA + 'MA-11', MA + 'MA-18', MA + 'MA-19'], 'automatic')]},
        'bundling_modifier_denial': {'binding_rules': [MA + 'MA-21', MA + 'V-422.202-notice-termination'], 'answer_by': [], 'interest': {'owed': 'contract', 'rules': [], 'how': ''},
                                     'next_escalation': [E('Dispute; ask for the guideline behind the edit and the dated written notice of any new edit or payment policy.', 'day 0', [MA + 'MA-21', MA + 'V-422.202-notice-termination'], 'automatic')]},
        'timely_filing_denial_with_proof': {'binding_rules': [MA + 'MA-03'], 'answer_by': [], 'interest': {'owed': 'contract', 'rules': [], 'how': ''},
                                            'next_escalation': [E('Dispute with proof of the plan\'s received date.', 'day 0', MA + 'MA-30', 'automatic')], 'gaps': ['Contract timely-filing terms are needed.']},
        'recoupment_takeback': {
            'binding_rules': [MA + 'V-plan-reopening-limits', MA + 'MA-19', 'payer_manuals/HUM-5', 'payer_manuals/UHC-4'],
            'answer_by': [],
            'interest': {'owed': 'contract', 'rules': [], 'how': ''},
            'filing_window': [A('UHC notice of disagreement', 30, 'calendar', 'recoupment_notice received', 'payer_manuals/UHC-4'),
                              A('Humana template: request review of an offset notice', 30, 'calendar', 'recoupment_notice_date', 'payer_manuals/HUM-5')],
            'next_escalation': [E('Dispute the takeback: past the contract lookback (Humana template 18 months) or without the advance notice, or a medical-necessity takeback of an authorized service.', 'day 0',
                                  ['payer_manuals/HUM-5', MA + 'MA-19', MA + 'V-plan-reopening-limits'], 'automatic')]},
        'credentialing_enrollment_denial': {'binding_rules': [], 'answer_by': [], 'interest': {'owed': 'contract', 'rules': [], 'how': ''},
                                            'next_escalation': [], 'gaps': ['No MA credentialing rule was researched.']},
    },
}
