export const meta = {
  name: 'phase2-unit',
  description: 'Push 2 phase 2: one unit (C1 API and worklist pages, C2 EMA pages Today/Data/policy, or R Results and ROI) builds, two independent reviews, then a fix',
  phases: [
    { title: 'Build', detail: 'the unit builds its part' },
    { title: 'Review', detail: 'two independent reviews with different lenses' },
    { title: 'Fix', detail: 'the fixer checks and fixes the findings' },
  ],
}
const S = '/tmp/claude-0/-home-user-Billing-Optimizer/96e81489-2ad2-5c70-991d-e00db2e22749/scratchpad'
const W = `${S}/wire`
const U = args.unit
const EASE = 'ease of use, as two personas in Chromium (Playwright) on a store built from fixtures (no real patient data in screenshots) at 1400 px and 390 px: a newcomer with no billing experience must finish the daily tasks without help; an experienced biller must never hit an unnecessary step or a lecture; one obvious primary action per item; plain words; no em dashes; no jargon; every number equals the list it opens; nothing blocks while loading; keyboard shortcuts work'
const UNITS = {
  C1: { title: 'the API and the worklist pages (api.py, index.html, desk.js, features.js, faxes.js, claim.js, actions.py hide grain)',
        brief: `${W}/brief_C.md (section "Unit C1", the shared rules, and the ADDED section at the end)`,
        lenses: [
          'money and safety: the bulk appeal list (B\'s exact rule AND next_step appeal_ok; both switch states; the left-out count and list), the hide grain and its migration of existing rules and journal replay, every next-step button (a fax only to a confirmed number with a preview; never an appeal of a paid, settled, credited, reprocessed or waiting line; write-offs only through the policy table or a person with a reason), counts equal lists, both hosts (server and serverless dashboard copy), auth on every new route',
          EASE ] },
  C2: { title: 'the EMA pages (api_ema.py, today.js, the new data.js Data tab, policy.js)',
        brief: `${W}/brief_C.md (section "Unit C2", the shared rules, and the ADDED section at the end)`,
        lenses: [
          'truth of every number: each count and dollar figure on Today and the Data tab equals its list and a direct SQL recomputation; on Vercel the routes read what the run stored in meta and never recompute EMA figures from the dashboard copy; the policy decisions (who, when, owner gate per the existing hide rule), held-report decisions incl. LedgerHandle.clear_attempt on serverless; CSVs carry no patient data; auth on every new route',
          EASE ] },
  R:  { title: 'Results and ROI (results.py, api_results.py, results.js, the scoreboard route, spot checks)',
        brief: `${W}/brief_R.md (including the owner's decision on the headline and the ADDED section at the end)`,
        lenses: [
          'honesty of attribution: re-derive 30 sampled recovered dollars by hand from raw rows; never count money paid before an item was first flagged; one payment counted once; a takeback reverses an attribution; the lift with small counts says "not enough cases yet"; hours saved use the editable minutes and keep the values in force; snapshots never change after the fact; spot checks sample and record correctly; counts equal lists; both hosts',
          EASE ] },
}
const u = UNITS[U]
if (!u) throw new Error('unknown unit ' + U)
const OUT = { type: 'object', properties: {
  summary: { type: 'string' }, files_changed: { type: 'array', items: { type: 'string' } },
  tests: { type: 'object', additionalProperties: { type: 'string' } },
  acceptance: { type: 'array', items: { type: 'object', properties: { check: { type: 'string' }, ok: { type: 'boolean' }, got: { type: 'string' } }, required: ['check', 'ok', 'got'] } },
  needs_for_others: { type: 'array', items: { type: 'string' } },
  open_questions: { type: 'array', items: { type: 'string' } },
  risks: { type: 'array', items: { type: 'string' } },
}, required: ['summary', 'files_changed', 'tests', 'acceptance', 'needs_for_others', 'risks'] }
const FIND = { type: 'object', properties: { findings: { type: 'array', items: { type: 'object', properties: {
  severity: { type: 'string', enum: ['high', 'medium', 'low'] }, file: { type: 'string' }, title: { type: 'string' },
  evidence: { type: 'string' }, fix: { type: 'string' } }, required: ['severity', 'file', 'title', 'evidence', 'fix'] } } }, required: ['findings'] }
const COMMON = `Repository: /home/user/Billing-Optimizer (branch claude/ecstatic-fermi-orfcgo; do not commit, push or reset). Scratchpad S = ${S}.
Read ${W}/PLAN.md (the owner's words, NON-NEGOTIABLES, the revised schedule, release gates and CPU rule at the end). PHI stays in
sqlite files under your work dir ${W}/${U}/; everything you print, return or write as text is aggregate or uses RemitWatch
finding ids; screenshots use fixtures or names off. Run scripts that read real data with python3 -I. Other agents edit other
files at the same time (phase 1 fixers, INT, the phase-3a module units E, G, H): never edit a file you do not own; ask in
${W}/needs_${U}.md.`
phase('Build')
const built = await agent(`${COMMON}
YOUR ROLE: unit ${U}, ${u.title}. Your brief: ${u.brief}. Read every ${W}/report_*.md and needs_*.md first (anything aimed at your
files is yours to do or to answer) and ${W}/brief_3a.md (interfaces fixed for later units). Build it completely with tests
(invented data, both hosts, break and restore for each test; node checks and Playwright screenshots for pages), acceptance on real
data as your brief says (one world in ${W}/${U}/, deleted at the end), ${W}/needs_${U}.md, and return the schema.`, { label: `build:${U}`, phase: 'Build', schema: OUT })
phase('Review')
const reviews = await parallel(u.lenses.map((lens, i) => () => agent(`${COMMON}
YOUR ROLE: independent reviewer ${i + 1} of unit ${U} (${u.title}). Do not edit repository files. Read the brief (${u.brief}), the
unit's needs file ${W}/needs_${U}.md, and its code, pages and tests. The builder's report: ${JSON.stringify(built).slice(0, 12000)}
Lens: ${lens}. For each problem: a failing test with invented data in a scratch copy under ${W}/${U}/review${i + 1}/ (never in the
repository), a Playwright screenshot, or a precise reading of the code that proves it; give file, title, evidence, severity and
the fix. Real problems only; say plainly when something holds up. Return the schema.`, { label: `review:${U}:${i + 1}`, phase: 'Review', schema: FIND })))
phase('Fix')
const findings = reviews.filter(Boolean).flatMap(r => r.findings || [])
const fixed = await agent(`${COMMON}
YOUR ROLE: fixer for unit ${U} (${u.title}); you own the unit's files (brief: ${u.brief}). The builder's report:
${JSON.stringify(built).slice(0, 10000)}
The reviewers' findings (check each yourself first; fix every high and medium that holds up and the small safe lows; say why any
is not fixed): ${JSON.stringify(findings).slice(0, 16000)}
Each fix gets a failing test first, then break and restore. Re-run the acceptance the fixes touch. At the end run the full suite
ONCE in ${S}/fix/venv2_compat312/bin/python and ${S}/fix/venv_sqlite340/bin/python (serialize-only failures expected there) plus
node tests/calc_model_check.js and node tests/lab_review_check.js; other units' half-done files can fail tests that are not
yours: check git diff before acting. Update ${W}/needs_${U}.md. Return the schema (it is saved as report_${U}.md).`, { label: `fix:${U}`, phase: 'Fix', schema: OUT })
return { unit: U, built, findings_count: findings.length, fixed }
