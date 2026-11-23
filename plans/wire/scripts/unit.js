export const meta = {
  name: 'push2-unit',
  description: 'Push 2: one unit builds from its brief, independent reviewers check it with different lenses, a fixer fixes what holds up',
  phases: [
    { title: 'Build', detail: 'the unit builds from its brief' },
    { title: 'Review', detail: 'independent reviews with different lenses' },
    { title: 'Fix', detail: 'the fixer checks and fixes the findings' },
  ],
}
const S = '/tmp/claude-0/-home-user-Billing-Optimizer/96e81489-2ad2-5c70-991d-e00db2e22749/scratchpad'
const W = `${S}/wire`
const U = args.unit, TITLE = args.title, BRIEF = args.brief, LENSES = args.lenses || []
if (!U || !TITLE || !BRIEF || !LENSES.length) throw new Error('args need unit, title, brief, lenses')
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
Read ${W}/PLAN.md (the owner's words, NON-NEGOTIABLES, the revised schedule, release gates, CPU rule, needs sweep and orchestrator
decisions at the end). PHI stays in sqlite files under ${W}/${U}/; everything you print, return or write as text is aggregate or
uses RemitWatch finding ids; screenshots use fixtures or names off. Run scripts that read real data with python3 -I. Other agents
edit other files at the same time: never edit a file you do not own; ask in ${W}/needs_${U}.md.`
phase('Build')
const built = await agent(`${COMMON}
YOUR ROLE: unit ${U}, ${TITLE}. Your brief: ${BRIEF} (read it in full, then every ${W}/report_*.md and needs_*.md). Do everything
in the brief with tests (invented data, both hosts, break and restore for each), measure as it says, write ${W}/needs_${U}.md,
delete your world copies at the end, and return the schema.`, { label: `build:${U}`, phase: 'Build', schema: OUT })
phase('Review')
const reviews = await parallel(LENSES.map((lens, i) => () => agent(`${COMMON}
YOUR ROLE: independent reviewer ${i + 1} of unit ${U} (${TITLE}). Do not edit repository files. Read the brief (${BRIEF}),
${W}/needs_${U}.md and the code the unit changed. The unit's report: ${JSON.stringify(built).slice(0, 14000)}
Lens: ${lens}. Work in ${W}/${U}/review${i + 1}/ (delete any world you build there at the end). For each problem: a failing test
with invented data in a scratch copy (never in the repository), a Playwright screenshot, or the exact measurement or reading of
the code that proves it; file, title, evidence, severity and the fix. Real problems only; say plainly what holds up.
Return the schema.`, { label: `review:${U}:${i + 1}`, phase: 'Review', schema: FIND })))
phase('Fix')
const findings = reviews.filter(Boolean).flatMap(r => r.findings || [])
const fixed = await agent(`${COMMON}
YOUR ROLE: fixer for unit ${U} (${TITLE}); you own the unit's files (brief: ${BRIEF}). The unit's report:
${JSON.stringify(built).slice(0, 10000)}
The reviewers' findings (check each yourself first; fix every high and medium that holds up and the small safe lows; say why any
is not fixed): ${JSON.stringify(findings).slice(0, 18000)}
Each fix gets a failing test first, then break and restore. Re-run the acceptance the fixes touch (world copies in ${W}/${U}/fix/,
deleted at the end). At the end run the full suite ONCE in ${S}/fix/venv2_compat312/bin/python and ${S}/fix/venv_sqlite340/bin/python
(serialize-only failures expected there) plus node tests/calc_model_check.js and node tests/lab_review_check.js if you touched a
page. Update ${W}/needs_${U}.md. Return the schema (it is saved as report_${U}.md).`, { label: `fix:${U}`, phase: 'Fix', schema: OUT })
return { unit: U, built, findings_count: findings.length, fixed }
