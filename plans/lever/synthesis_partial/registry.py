"""Rule registry for the leverage synthesis.

Loads every research.json and verify.json under lever/<area>/, keeps the verifier's citation, URL and quote, and adds the
rules a verifier found and quoted on its own ("missing" lists). Operative quotes used by letters are checked to be verbatim
substrings of the verified text (typographic quotes and whitespace normalized). Public law only; no patient data.
"""
import json
import os
import re

BASE = '/tmp/claude-0/-home-user-Billing-Optimizer/96e81489-2ad2-5c70-991d-e00db2e22749/scratchpad/lever/'
AREAS = ['medicare_ffs', 'medicare_advantage', 'kentucky', 'indiana', 'erisa_commercial', 'payer_manuals',
         'tactics_and_risk', 'va_tricare']


def norm(s):
    s = (s or '').replace('“', '"').replace('”', '"').replace('‘', "'").replace('’', "'")
    s = s.replace(' ', ' ')
    return re.sub(r'\s+', ' ', s).strip()


def load():
    reg, missing = {}, {}
    for area in AREAS:
        r = json.load(open(BASE + area + '/research.json'))
        v = json.load(open(BASE + area + '/verify.json'))
        vc = {c['id']: c for c in v['checks']}
        for rule in r['rules']:
            c = vc[rule['id']]
            key = f"{area}/{rule['id']}"
            status = {'correct': 'verified', 'corrected': 'corrected', 'could_not_check': 'could_not_check'}[c['verdict']]
            reg[key] = {
                'key': key, 'area': area, 'source_id': rule['id'], 'status': status,
                'citation': c.get('citation') or rule['citation'],
                'url': c.get('url') or rule['url'],
                'verified_quote': c.get('quote') or rule['quote'],
                'researcher_quote': rule['quote'],
                'verifier_note': c.get('correction') or '',
                'applies_to': rule.get('applies_to', ''),
                '_researcher_fields': ' '.join(rule.get(k, '') or '' for k in ('requires', 'deadline', 'consequence', 'applies_to')),
            }
        rids = {x['id'] for x in r['rules']}
        for c in v['checks']:
            if c['id'] in rids:
                continue
            key = f"{area}/{c['id']}"
            status = {'correct': 'verified', 'corrected': 'corrected', 'could_not_check': 'could_not_check'}[c['verdict']]
            reg[key] = {'key': key, 'area': area, 'source_id': c['id'], 'status': status, 'citation': c.get('citation', ''),
                        'url': c.get('url', ''), 'verified_quote': c.get('quote', ''), 'researcher_quote': '',
                        'verifier_note': c.get('correction', ''), 'applies_to': '', '_researcher_fields': '',
                        'extra_check': True}
        missing[area] = v.get('missing', [])
    return reg, missing


# Rules the verifiers found and quoted themselves (their "missing" lists). (key, area, unique prefix of the missing entry,
# citation, url, scope note). The whole missing entry is kept as the verified text.
VERIFIER_ADDED = [
    ('medicare_ffs/V-405.374-rebuttal', 'medicare_ffs', '42 CFR 405.373(a), 405.374(a)-(b)',
     '42 CFR 405.373(a), 405.374(a)-(b), 405.375(a)-(c)', 'https://www.ecfr.gov/current/title-42/section-405.374',
     'Traditional Medicare overpayment offsets and recoupments.'),
    ('medicare_ffs/V-310.1.B.4-no-duplicates', 'medicare_ffs', 'MCPM Ch. 29 sec. 310.1.B.4',
     'MCPM Pub. 100-04 Ch. 29 sec. 310.1.B.4', 'https://www.cms.gov/regulations-and-guidance/guidance/manuals/downloads/clm104c29.pdf',
     'All Medicare redeterminations.'),
    ('medicare_ffs/V-310.1-NOTE-mail', 'medicare_ffs', 'MCPM Ch. 29 sec. 310.1 NOTE',
     'MCPM Pub. 100-04 Ch. 29 sec. 310.1 NOTE', 'https://www.cms.gov/regulations-and-guidance/guidance/manuals/downloads/clm104c29.pdf',
     'All MAC appeal intake.'),
    ('medicare_ffs/V-esMD', 'medicare_ffs', 'esMD as a no-fax electronic channel',
     'CMS esMD pages; CGS notice of 07/16/2024', 'https://www.cms.gov/data-research/computer-data-systems/esmd/esmd-medicare-providers-and-suppliers',
     'Channel option only. The CGS notice is on its HHH site; confirm Part B esMD intake with CGS before use.'),
    ('medicare_ffs/V-1882c3-medigap-crossover', 'medicare_ffs', 'SSA 1882(c)(3)(A)-(B)',
     'SSA 1882(c)(3)(A)-(B) [42 USC 1395ss(c)(3)]', 'https://www.law.cornell.edu/uscode/text/42/1395ss',
     'Medicare supplement (Medigap) policies; participating physicians.'),
    ('medicare_advantage/V-guidance-10.5-receipt', 'medicare_advantage', 'Fax receipt starts the clock',
     'Parts C&D Enrollee Grievances, Organization/Coverage Determinations, and Appeals Guidance secs. 10.5.1, 10.5.2',
     'https://www.cms.gov/medicare/appeals-and-grievances/mmcag/downloads/parts-c-and-d-enrollee-grievances-organization-coverage-determinations-and-appeals-guidance.pdf',
     'All MA plans, requests to the plan (enrollee and non-contracted provider appeals).'),
    ('medicare_advantage/V-plan-reopening-limits', 'medicare_advantage', 'Plan-initiated reopenings and takebacks are time-limited',
     '42 CFR 422.616(a); 42 CFR 405.980(b)(1)-(3); 405.986(a); CMS HPMS FAQ (Feb. 6, 2024) Q9',
     'https://www.ecfr.gov/current/title-42/section-405.980',
     'MA plan reopenings; certain for non-contracted claims, for contracted claims at least through the FAQ.'),
    ('medicare_advantage/V-422.122c-pa-stats', 'medicare_advantage', 'Plans must publish their prior authorization statistics',
     '42 CFR 422.122(c)', 'https://www.ecfr.gov/current/title-42/section-422.122', 'All MA organizations.'),
    ('medicare_advantage/V-star-ratings-money', 'medicare_advantage', 'Why Star Ratings are real money',
     '42 CFR 422.258(d)(7)(i)(C); 422.266(a)(2)(ii); 422.260(b); 422.510(a)(4)(xi)', 'https://www.ecfr.gov/current/title-42/part-422',
     'MA contracts.'),
    ('medicare_advantage/V-plan-answers-for-vendors', 'medicare_advantage', 'The plan answers for its vendors',
     '42 CFR 422.504(i)(1); 42 CFR 422.562(a)(3)', 'https://www.ecfr.gov/current/title-42/section-422.504', 'All MA organizations.'),
    ('medicare_advantage/V-422.202-notice-termination', 'medicare_advantage', 'Contracted plans: notice duties, and the retaliation risk',
     '42 CFR 422.202(a)(1)-(2), (d)(4)', 'https://www.ecfr.gov/current/title-42/section-422.202',
     'MA coordinated care plans and contracted physicians.'),
    ('medicare_advantage/V-new-evidence-allowed', 'medicare_advantage', 'New evidence is allowed later in MA appeals',
     '42 CFR 422.562(d)(2)(vi); 42 CFR 422.586', 'https://www.ecfr.gov/current/title-42/section-422.562', 'MA appeals (Subpart M).'),
    ('medicare_advantage/V-outreach-records', 'medicare_advantage', 'Records-missing denials and post-service authorization denials',
     'Parts C&D Appeals Guidance secs. 40.12.1 and 10.6',
     'https://www.cms.gov/medicare/appeals-and-grievances/mmcag/downloads/parts-c-and-d-enrollee-grievances-organization-coverage-determinations-and-appeals-guidance.pdf',
     'All MA plans, payment and retrospective decisions.'),
    ('medicare_advantage/V-network-gaps', 'medicare_advantage', 'Network gaps and plan-directed care',
     'MMCM ch. 4 secs. 110.1.3 and 160; 42 CFR 422.105(a)', 'https://www.cms.gov/regulations-and-guidance/guidance/manuals/downloads/mc86c04.pdf',
     'MA plans; referred or plan-arranged out-of-network care.'),
    ('kentucky/V-700-9-ky-licensed', 'kentucky', 'KRS 304.17A-700(9) with',
     'KRS 304.17A-700(9); 304.17A-005(23)(h); KRS 205.532(1)(g); KRS 205.646(1)(e)',
     'https://apps.legislature.ky.gov/law/statutes/statute.aspx?id=48419',
     'Gate on every Kentucky prompt-pay and Medicaid MCO citation: only providers licensed in Kentucky.'),
    ('kentucky/V-situs', 'kentucky', 'KRS 304.17A-005(22)(a)7 and KRS 304.17A-603(1)',
     'KRS 304.17A-005(22)(a)7; KRS 304.17A-603(1)', 'https://apps.legislature.ky.gov/law/statutes/statute.aspx?id=54530',
     'Which plans Kentucky law reaches.'),
    ('kentucky/V-607-filed-ur-policies', 'kentucky', 'KRS 304.17A-607(2)(l) and (4)(b)',
     'KRS 304.17A-607(2)(l), (4)(b); KRS 304.17A-613(6)', 'https://apps.legislature.ky.gov/law/statutes/statute.aspx?id=57342',
     'Kentucky insurers and private review agents.'),
    ('kentucky/V-600-16b-pricing-not-ur', 'kentucky', 'KRS 304.17A-600(16)(b)',
     'KRS 304.17A-600(16)(b)', 'https://apps.legislature.ky.gov/law/statutes/statute.aspx?id=57341', 'Kentucky external review scope.'),
    ('kentucky/V-235-4-new-products', 'kentucky', 'KRS 304.17A-235(4)',
     'KRS 304.17A-235(4)', 'https://apps.legislature.ky.gov/law/statutes/statute.aspx?id=45438',
     'Kentucky insurers and participating providers.'),
    ('kentucky/V-205.534-2-pa-clock', 'kentucky', 'KRS 205.534(2) (effective 4/14/2026)',
     'KRS 205.534(2); KRS 205.522(1)(m); Aetna CY2026 KY Medicaid contract Appendix A rows 36 and 59',
     'https://apps.legislature.ky.gov/law/statutes/statute.aspx?id=57033', 'Kentucky Medicaid MCOs.'),
    ('kentucky/V-205.536-criteria', 'kentucky', 'KRS 205.536(1)-(2) with KRS 304.38-240',
     'KRS 205.536(1)-(2); KRS 304.38-240', 'https://apps.legislature.ky.gov/law/statutes/statute.aspx?id=55333',
     'Kentucky Medicaid MCO utilization review.'),
    ('kentucky/V-907-17-035-s6-specialist', 'kentucky', '907 KAR 17:035 Sec. 6',
     '907 KAR 17:035 Sec. 6', 'https://apps.legislature.ky.gov/law/kar/downloads/docs/9950/document.engrossed.pdf',
     'Kentucky Medicaid MCO medical-necessity appeals.'),
    ('kentucky/V-907-17-010-s5-5-automatic-ruling', 'kentucky', '907 KAR 17:010 Sec. 5(5)',
     '907 KAR 17:010 Sec. 5(3)-(5); Sec. 4(3); 907 KAR 17:035 Sec. 2(4)',
     'https://apps.legislature.ky.gov/law/kar/downloads/docs/11020/document.engrossed.pdf',
     'Kentucky Medicaid enrollee fair hearings only (with the enrollee\'s specific written consent).'),
    ('kentucky/V-mco-overpayment-permission', 'kentucky', 'Kentucky Medicaid MCO contract (verified in Aetna CY2026',
     'Kentucky Medicaid MCO contract CY2026 (Aetna verified), Attachment C Sec. 34.0 item 1.G, Appendix I (e)(2), Appendix A rows 35 and 73; KRS 205.5356(4)',
     'https://www.chfs.ky.gov/agencies/dms/dhpo/Documents/CY2026Aetna.pdf',
     'Kentucky Medicaid MCO overpayment collection over $500 (Aetna contract verified; other MCO contracts not read).'),
    ('kentucky/V-806-17-370-secondary-attachments', 'kentucky', '806 KAR 17:370 Sec. 2-3',
     '806 KAR 17:370 Sec. 2-3; 806 KAR 17:360 Sec. 2(1)', 'https://apps.legislature.ky.gov/law/kar/downloads/docs/10592/document.engrossed.pdf',
     'Kentucky insurers, secondary claims.'),
    ('kentucky/V-tpa-license', 'kentucky', 'KRS 304.9-051(1)(a), 304.9-052(1)',
     'KRS 304.9-051(1)(a); 304.9-052(1); 304.99-020(1)(c); 304.2-160(3)-(4)',
     'https://apps.legislature.ky.gov/law/statutes/statute.aspx?id=57325',
     'Administrators adjusting claims in Kentucky. Whether DOI acts on a self-funded ERISA plan claim is not confirmed.'),
    ('kentucky/V-current-mcos', 'kentucky', 'DMS MCO contracts page',
     'DMS MCO Contracts page (read 11/23/2026)', 'https://www.chfs.ky.gov/agencies/dms/dhpo/Pages/MCO-Contracts.aspx',
     'Which Kentucky Medicaid MCOs are current.'),
    ('indiana/V-37.5-25-start-window', 'indiana', 'IC 27-1-37.5-25 (SEA 480',
     'IC 27-1-37.5-25', 'https://iga.in.gov/ic/2026/Title_27.html', 'Indiana UR entities (scope as IC 27-1-37.5).'),
    ('indiana/V-37.5-26-27-auth-validity', 'indiana', 'IC 27-1-37.5-26(b) and -27',
     'IC 27-1-37.5-26(b); 27-1-37.5-27(a), (c), (d)', 'https://iga.in.gov/ic/2026/Title_27.html', 'Indiana UR entities.'),
    ('indiana/V-37.5-19-posted-criteria', 'indiana', 'IC 27-1-37.5-19(a),(b)',
     'IC 27-1-37.5-19(a)-(b); 27-1-37.5-28', 'https://iga.in.gov/ic/2026/Title_27.html', 'Indiana UR entities.'),
    ('indiana/V-37.5-20-clinical-peer', 'indiana', 'IC 27-1-37.5-20 with -1.7 and -21',
     'IC 27-1-37.5-20, -1.7, -21', 'https://iga.in.gov/ic/2026/Title_27.html', 'Indiana UR entities.'),
    ('indiana/V-37.5-17-23-peer-to-peer', 'indiana', 'IC 27-1-37.5-17 and -23(c)-(g)',
     'IC 27-1-37.5-17; 27-1-37.5-23(c)-(g)', 'https://iga.in.gov/ic/2026/Title_27.html', 'Indiana UR entities.'),
    ('indiana/V-27-4-1-19-annual-report', 'indiana', 'IC 27-4-1-19(a)(2)',
     'IC 27-4-1-19(a)(2)', 'https://iga.in.gov/ic/2026/Title_27.html', 'Indiana insurers.'),
    ('indiana/V-27-1-3-19-cease-order', 'indiana', 'IC 27-1-3-19:',
     'IC 27-1-3-19', 'https://iga.in.gov/ic/2026/Title_27.html', 'Indiana insurers (enforcement route for duties with no penalty of their own).'),
    ('indiana/V-27-8-11-11-oon-check-notice', 'indiana', 'IC 27-8-11-11:',
     'IC 27-8-11-11', 'https://iga.in.gov/ic/2026/Title_27.html', 'Indiana insurers paying insureds for non-contracted providers.'),
    ('indiana/V-27-8-11-13.5-provisional-move', 'indiana', 'IC 27-8-11-13.5 (P.L.215-2025)',
     'IC 27-8-11-13.5', 'https://iga.in.gov/ic/2026/Title_27.html', 'Indiana insurers; physicians changing employers or locations in Indiana.'),
    ('indiana/V-27-1-25-12.4-tpa-license', 'indiana', 'IC 27-1-25-12.4(b)(4)',
     'IC 27-1-25-12.4(b)(4); IC 27-1-25-1(a), (l)', 'https://iga.in.gov/ic/2026/Title_27.html',
     'Indiana TPAs for insurers, HMOs, governmental and church plans; not private self-funded ERISA plans.'),
    ('indiana/V-12-15-13-7.2-code-versions', 'indiana', 'IC 12-15-13-7.2:',
     'IC 12-15-13-7.2', 'https://iga.in.gov/ic/2026/Title_12.html', 'Indiana Medicaid office, which includes MCOs (IC 12-15-13-0.4).'),
    ('indiana/V-12-15-13-3.5-overpayments', 'indiana', 'IC 12-15-13-3.5',
     'IC 12-15-13-3.5', 'https://iga.in.gov/ic/2026/Title_12.html', 'Indiana Medicaid noninstitutional provider overpayments; reach to MCE recoupments not confirmed.'),
    ('indiana/V-medigap-prompt-pay', 'indiana', 'Medigap and supplemental policies: IC 27-8-5.7-1',
     'IC 27-8-5.7-1; IC 27-8-5-1', 'https://iga.in.gov/ic/2026/Title_27.html',
     'Indiana-issued Medicare supplement policies, on the text; not confirmed by IDOI.'),
    ('indiana/V-27-8-28-13-notice-with-denial', 'indiana', 'IC 27-8-28-13 and IC 27-13-10-4',
     'IC 27-8-28-13; IC 27-13-10-4', 'https://iga.in.gov/ic/2026/Title_27.html', 'Indiana insurers and HMOs.'),
    ('erisa_commercial/V-uhc-delegate-standard', 'erisa_commercial', "UnitedHealthcare 2026 Administrative Guide, 'Capitation",
     "2026 UnitedHealthcare Care Provider Administrative Guide, 'Capitation and/or delegation supplement', 'ERISA claims processing'",
     'https://www.uhcprovider.com/content/dam/provider/docs/public/admin-guides/2026-UHC-Administrative-Guide.pdf',
     "UHC's own published standard for its delegated claim payers; not federal law (ERISA has no payment deadline)."),
    ('payer_manuals/V-kdms-mco-dispute-form', 'payer_manuals', 'KENTUCKY MEDICAID STATE ESCALATION',
     'KDMS Contract Monitoring Branch MCO Dispute Form (Rev 09/25/2025)', 'https://www.chfs.ky.gov/agencies/dms/MAPForms/MCODisputeForm.pdf',
     'All Kentucky Medicaid MCOs (form lists Aetna, Anthem, Humana, UnitedHealthcare, Passport, WellCare).'),
    ('payer_manuals/V-205.534-3a4-monthly-report', 'payer_manuals', 'KRS 205.534(3)(a)4',
     'KRS 205.534(3)(a)4', 'https://apps.legislature.ky.gov/law/statutes/statute.aspx?id=57033', 'Kentucky Medicaid MCOs.'),
    ('payer_manuals/V-uhc-cp-ky-manual', 'payer_manuals', 'UnitedHealthcare Community Plan of Kentucky 2026',
     'UnitedHealthcare Community Plan of Kentucky 2026 Care Provider Manual, Chapter 12 grid p. 69',
     'https://www.uhcprovider.com/content/dam/provider/docs/public/admin-guides/comm-plan/KY-Care-Provider-Manual.pdf',
     'UnitedHealthcare Community Plan (Kentucky Medicaid).'),
    ('payer_manuals/V-anthem-com-overpayment-45', 'payer_manuals', 'Anthem commercial (KY; same structure in IN) Notice of Overpayment',
     '2026 Anthem BCBS Kentucky Commercial Provider Manual pp. 117-119 (Notice of Overpayment and Claims Audit Appeal Policy)',
     'https://www.anthem.com/content/dam/digital/docs/provider/commercial/manuals/PM_KY_00011.pdf',
     'Anthem commercial (Kentucky; same structure in Indiana).'),
    ('payer_manuals/V-anthem-com-clinical-appeal', 'payer_manuals', 'Anthem commercial clinical appeals',
     '2026 Anthem BCBS Kentucky Commercial Provider Manual pp. 60-61', 'https://www.anthem.com/content/dam/digital/docs/provider/commercial/manuals/PM_KY_00011.pdf',
     'Anthem commercial clinical (medical-necessity) appeals.'),
    ('payer_manuals/V-uhc-retro-eligibility-notice', 'payer_manuals', 'UnitedHealthcare retroactive-eligibility refunds',
     '2026 UnitedHealthcare Administrative Guide p. 146', 'https://www.uhcprovider.com/content/dam/provider/docs/public/admin-guides/2026-UHC-Administrative-Guide.pdf',
     'UnitedHealthcare network providers.'),
    ('tactics_and_risk/V-617-4-appeal-reviewer', 'tactics_and_risk', 'KRS 304.17A-617(4)(a)-(b), (4)(d)2, (5)',
     'KRS 304.17A-617(4)(a)-(b), (4)(d)2, (5)(c)', 'https://apps.legislature.ky.gov/law/statutes/statute.aspx?id=57343',
     'Kentucky insurers under KRS 304.17A (not Medicaid MCOs for 617).'),
    ('tactics_and_risk/V-27-1-52-8-13', 'tactics_and_risk', 'IC 27-1-52-8 and 27-1-52-13',
     'IC 27-1-52-8; IC 27-1-52-13', 'https://iga.in.gov/ic/2026/Title_27.html', 'Indiana insurers and HMOs; not Medicaid.'),
    ('tactics_and_risk/V-27-13-15-1d-rate-schedule', 'tactics_and_risk', 'IC 27-13-15-1(d)',
     'IC 27-13-15-1(d)', 'https://iga.in.gov/ic/2026/Title_27.html', 'Indiana HMOs and participating providers.'),
    ('va_tricare/V-199.7h4-under-1-dollar', 'va_tricare', '32 CFR 199.7(h)(4)',
     '32 CFR 199.7(h)(4)', 'https://www.ecfr.gov/current/title-32/section-199.7', 'TRICARE.'),
    ('va_tricare/V-199.8-ma-cost-sharing', 'va_tricare', '32 CFR 199.8(d)(1)(vii)',
     '32 CFR 199.8(d)(1)(vii)-(viii)', 'https://www.ecfr.gov/current/title-32/section-199.8', 'TRICARE secondary to MA plans and Medigap.'),
    ('va_tricare/V-199.11-recoupment', 'va_tricare', '32 CFR 199.11(f)(6)(ii)(B)-(E)',
     '32 CFR 199.11(d), (f)(4), (f)(6)(ii)(B)-(E), (f)(6)(v)', 'https://www.ecfr.gov/current/title-32/section-199.11', 'TRICARE recoupments.'),
    ('va_tricare/V-199.17-state-law-preempted', 'va_tricare', '32 CFR 199.17(a)(7)(ii)',
     '32 CFR 199.17(a)(7)(ii)', 'https://www.ecfr.gov/current/title-32/section-199.17', 'TRICARE regional contracts.'),
    ('va_tricare/V-199.10-b4-notice-contents', 'va_tricare', '32 CFR 199.10(b)(4)(i)-(v)',
     '32 CFR 199.10(b)(4)(i)-(v), (a)(1)(i)(F), (a)(9)', 'https://www.ecfr.gov/current/title-32/section-199.10', 'TRICARE appeals.'),
    ('va_tricare/V-1095c-note-731', 'va_tricare', '10 U.S.C. 1095c note',
     '10 U.S.C. 1095c note, Pub. L. 109-364, div. A, title VII, sec. 731(a)-(b)', 'https://uscode.house.gov/us/usc/t10/s1095c/', 'TRICARE.'),
    ('va_tricare/V-optum-records-duty', 'va_tricare', 'Optum Medical Service Model and Claims Guide (version 2026 1104), p. 1',
     'Optum VA CCN Medical Service Model and Claims Guide (version 2026 1104), p. 1',
     'https://vacommunitycare.com/api/vaccnportal/v1/webcomponentcode/trainingdoc/ccnClaims/ccnMedServClaimGuide', 'VA CCN providers.'),
    ('va_tricare/V-optum-cms-edits', 'va_tricare', 'Optum VA CCN Provider Manual (version 2026 1102), p. 47',
     'Optum VA CCN Provider Manual (version 2026 1102), pp. 43, 47',
     'https://vacommunitycare.com/api/vaccnportal/v1/webcomponentcode/trainingdoc/ccnProvManual/ccnPrManual', 'VA CCN claims.'),
    ('va_tricare/V-hm-unauthorized-penalty', 'va_tricare', 'Humana Military TRICARE Provider Handbook, East Region 2026, p. 18',
     'Humana Military TRICARE Provider Handbook, East Region 2026, p. 18',
     'https://www.humanamilitary.com/content/dam/sites/humana-military-com/provider/tricare-provider-handbook.pdf', 'TRICARE East network providers.'),
    ('va_tricare/V-va-supporting-docs', 'va_tricare', "VA Community Care 'Provider claims' page (updated Oct. 20, 2026)",
     "VA Community Care 'Provider claims' page (updated Oct. 20, 2026)", 'https://department.va.gov/vha/community-care/provider-claims/',
     'Claims VA processes itself (VCA, VA-paid); not Optum CCN claims.'),
    ('va_tricare/V-84fr26301-ccn-terms', 'va_tricare', '84 FR 26301 (June 5, 2019)',
     '84 FR 26301 (June 5, 2019)', 'https://www.govinfo.gov/content/pkg/FR-2019-06-05/html/2019-11575.htm', 'VA CCN.'),
]


def add_verifier_added(reg, missing):
    for key, area, prefix, cit, url, scope in VERIFIER_ADDED:
        hits = [m for m in missing[area] if m.startswith(prefix)]
        if len(hits) != 1:
            raise SystemExit(f'verifier-added {key}: {len(hits)} matches for prefix {prefix!r}')
        reg[key] = {'key': key, 'area': area, 'source_id': key.split('/', 1)[1], 'status': 'verifier_added', 'citation': cit,
                    'url': url, 'verified_quote': hits[0], 'researcher_quote': '', 'verifier_note': '', 'applies_to': scope,
                    '_researcher_fields': ''}


def check_quote(rule, q):
    """A quote is accepted only when it is a verbatim substring of the verified text. For a rule a verifier marked correct,
    the researcher's own fields count too (the verifier confirmed them)."""
    pool = [rule['verified_quote'], rule['researcher_quote'], rule['verifier_note']]
    if rule['status'] == 'verified':
        pool.append(rule['_researcher_fields'])
    nq = norm(q)
    return any(nq in norm(p) for p in pool if p)
