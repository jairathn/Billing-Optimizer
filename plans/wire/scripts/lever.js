export const meta = {
  name: 'leverage-research',
  description: 'Research the legal and contractual leverage a dermatology practice (Kentucky and Indiana) has over each kind of payer when it appeals, disputes or chases a claim; every citation independently verified against the primary source; then a synthesis into a leverage matrix, an escalation ladder and letter blocks',
  phases: [
    { title: 'Research', detail: 'one researcher per area, primary sources only' },
    { title: 'Verify', detail: 'an independent verifier checks every citation against the primary text' },
    { title: 'Synthesize', detail: 'leverage matrix, escalation ladder, letter blocks, batch rules' },
  ],
}
const S = '/tmp/claude-0/-home-user-Billing-Optimizer/96e81489-2ad2-5c70-991d-e00db2e22749/scratchpad'
const L = `${S}/lever`
const AREAS = args.areas
const CTX = `CONTEXT. RemitWatch is software for a dermatology practice (Mohs surgery, radiation for skin cancer, pathology) in Kentucky and
Indiana (Medicare contractors CGS J15 for Kentucky and WPS J8 for Indiana). It finds money payers owe the practice (denials still
appealable, EMA write-offs still appealable, unanswered claims, open balances, underpayments against the payer's own rate,
payments not posted, secondaries never billed) and sends appeals and disputes by fax. The owner wants every letter to carry the
practice's REAL leverage so payers cannot ignore it or say no without real risk, and wants to know: do payers legally have to
answer, and by when; what happens if they do not. The payers that matter most (by dollars): Humana Medicare Advantage, Anthem /
BCBS (commercial and Medicare Advantage, Kentucky and Indiana), traditional Medicare (KY and IN), UnitedHealthcare (commercial,
MA, UHC Shared Services), UMR (self-funded plans, UHC's TPA), UMWA Health & Retirement Funds, Cigna, VA Community Care Network
(administered by Optum), Kentucky and Indiana Medicaid managed care plans (Humana, Anthem, WellCare, Aetna Better Health, Passport,
MHS / Managed Health Services in Indiana, CareSource), and Medigap / secondary payers.
RULES. Primary sources only for law and regulation (eCFR, the U.S. Code at uscode.house.gov or law.cornell.edu, the Kentucky
Revised Statutes at apps.legislature.ky.gov, Kentucky Administrative Regulations, the Indiana Code at iga.in.gov, Indiana
Administrative Code, CMS manuals and CMS.gov, state insurance department pages, payer provider manuals on the payer's own site).
For every rule give: the exact citation (section and subsection), the URL you read it at, a short verbatim quote of the operative
words, what it requires of the payer, to whom it applies (fully insured vs self-funded, contracted vs non-contracted, Medicare
Advantage vs traditional, Medicaid managed care), the deadline in days and from what event, the consequence if missed (interest
rate and from when, penalty, escalation, deemed exhaustion, regulator enforcement), and how a provider (not only a member) can use
it. Say plainly when something does NOT apply (for example ERISA preemption of state prompt-pay laws for self-funded plans) and when
you could not confirm something. Never guess a number or a citation. Save your notes and sources in ${L}/<your area>/ (no patient
data exists in this task). Return a structured result.`
const RES = { type: 'object', properties: {
  area: { type: 'string' }, summary: { type: 'string' },
  rules: { type: 'array', items: { type: 'object', properties: {
    id: { type: 'string' }, citation: { type: 'string' }, url: { type: 'string' }, quote: { type: 'string' }, applies_to: { type: 'string' },
    requires: { type: 'string' }, deadline: { type: 'string' }, consequence: { type: 'string' }, provider_use: { type: 'string' },
    confidence: { type: 'string', enum: ['verified_text', 'secondary_source', 'unconfirmed'] } },
    required: ['id', 'citation', 'url', 'quote', 'applies_to', 'requires', 'consequence', 'provider_use', 'confidence'] } },
  batch_and_channel: { type: 'array', items: { type: 'string' } },
  does_not_apply: { type: 'array', items: { type: 'string' } },
  open_questions: { type: 'array', items: { type: 'string' } } },
  required: ['area', 'summary', 'rules'] }
const VER = { type: 'object', properties: { area: { type: 'string' }, checks: { type: 'array', items: { type: 'object', properties: {
  id: { type: 'string' }, verdict: { type: 'string', enum: ['correct', 'corrected', 'wrong', 'could_not_check'] },
  citation: { type: 'string' }, url: { type: 'string' }, quote: { type: 'string' }, correction: { type: 'string' } },
  required: ['id', 'verdict', 'citation', 'url'] } }, missing: { type: 'array', items: { type: 'string' } } }, required: ['area', 'checks'] }
phase('Research')
const results = await pipeline(AREAS,
  a => agent(`${CTX}
YOUR AREA: ${a.key}. ${a.what}
At the end also save your structured result as JSON to ${L}/${a.key}/research.json.`, { label: `research:${a.key}`, phase: 'Research', schema: RES }),
  (r, a) => r ? agent(`${CTX}
YOUR ROLE: independent verifier for area ${a.key}. For EVERY rule below, open the primary source yourself (not the researcher's
notes), confirm the citation, the subsection, the deadline, the interest rate or consequence, and to whom it applies, and quote the
operative words exactly. Mark each correct, corrected (give the right citation, numbers and quote), wrong (it does not say that), or
could_not_check (say why). Then list important rules for this area the researcher missed (with citations you verified).
The researcher's result: ${JSON.stringify(r).slice(0, 45000)}
At the end also save your structured result as JSON to ${L}/${a.key}/verify.json.`, { label: `verify:${a.key}`, phase: 'Verify', schema: VER }).then(v => ({ area: a.key, research: r, verify: v })) : null,
)
if (args.synthesize === false) return { areas: results.filter(Boolean) }
phase('Synthesize')
const synth = await agent(`${CTX}
YOUR ROLE: synthesize the verified research into what RemitWatch's letters and escalations will use. Use ONLY rules a verifier
marked correct or corrected (use the corrected text); drop wrong ones; mark could_not_check ones as such. The material: every ${L}/<area>/research.json and verify.json (read them all; areas from other runs are there too), plus:
${JSON.stringify(results.filter(Boolean)).slice(0, 120000)}
Write these files in ${L}/:
1. leverage_matrix.json: for each payer kind (traditional Medicare by MAC, Medicare Advantage contracted / non-contracted, KY fully
   insured, IN fully insured, self-funded ERISA (TPA), KY Medicaid MCO, IN Medicaid MCO, VA CCN, Medigap/secondary) x each issue
   kind (denial to appeal, underpayment against the payer's own rate or contract, no answer to a clean claim, payment reported but
   not received, corrected claim refused, medical-necessity denial, bundling or modifier denial, timely-filing denial with proof,
   recoupment/takeback, credentialing/enrollment denial): the rules that bind the payer (citation, verified quote, URL), the
   deadline it must answer by and from what event, interest owed and how to compute it, what the denial must contain, what we may
   request (criteria, reviewer credentials, claim file), the next escalation step and when it opens, whether a batch (one letter,
   many claims) is accepted and on what form or channel, and what to never claim.
2. escalation_ladder.json: per payer kind, the ordered steps (first appeal or dispute, second level, external or independent review,
   regulator complaint, interest demand), each with the day it opens, the channel, the form, what must be attached, and whether it is
   a decision for the owner (anything that is a complaint to a regulator or affects the payer relationship) or automatic.
3. letter_blocks.md: the text blocks letters use, in plain firm professional English (short sentences, no em dashes, no AI
   phrasing, no adjectives doing the work of facts), with placeholders: the binding rule and its citation, the answer-by date, the
   interest statement, the precedent paragraph (the payer's own earlier payments of the same service), the request for basis,
   criteria and reviewer credentials, the escalation sentence with its date, and the batch cover letter. Every block cites only
   verified rules for that payer kind, and says nothing the practice will not actually do.
4. LEVERAGE_REPORT.md: for the owner, plain English: do payers have to answer (by payer kind, with the deadline and the
   consequence); our real leverage, strongest first; what is NOT leverage (and why a bluff backfires with automated reviewers);
   the risks of escalating (network relations, audits, retaliation) and how to manage them; what we need from the practice (its
   participation agreements and their dispute clauses, a signed assignment of benefits on file, who approves complaints).
Also follow the owner's decisions in ${S}/wire/PLAN.md, orchestrator decisions 12, 13, 15 and 16 (leverage that is true, specific and acted on; win every battle as the goal; proof of fax delivery enclosed in every follow-up and the answer clock counted from it; appeal at any win rate when expected value beats the cost, truthful always): give the escalation ladder the delivery-proof step, the route switch after unanswered requests, and the deemed-decision rules (where silence past a deadline decides for the provider or opens the next level), each with its verified citation.
Return a short summary with the counts of verified, corrected, dropped and unchecked rules.`, { label: 'synthesize', phase: 'Synthesize' })
return { areas: results.filter(Boolean).map(r => ({ area: r.area, rules: (r.research.rules || []).length, checks: (r.verify && r.verify.checks || []).length })), synth }
