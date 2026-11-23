export const meta = {
  name: 'push2-fix-only',
  description: 'Push 2: the fixer for one unit checks and fixes the findings its independent reviewers already returned',
  phases: [ { title: 'Fix', detail: 'the fixer checks and fixes the findings' } ],
}
const S = '/tmp/claude-0/-home-user-Billing-Optimizer/96e81489-2ad2-5c70-991d-e00db2e22749/scratchpad'
const W = `${S}/wire`
const U = args.unit, TITLE = args.title, BRIEF = args.brief, FINDINGS = args.findings || [], REPORT = args.report || ''
if (!U || !TITLE || !BRIEF) throw new Error('args need unit, title, brief')
const OUT = { type: 'object', properties: {
  summary: { type: 'string' }, files_changed: { type: 'array', items: { type: 'string' } },
  tests: { type: 'object', additionalProperties: { type: 'string' } },
  acceptance: { type: 'array', items: { type: 'object', properties: { check: { type: 'string' }, ok: { type: 'boolean' }, got: { type: 'string' } }, required: ['check', 'ok', 'got'] } },
  needs_for_others: { type: 'array', items: { type: 'string' } },
  open_questions: { type: 'array', items: { type: 'string' } },
  risks: { type: 'array', items: { type: 'string' } },
}, required: ['summary', 'files_changed', 'tests', 'acceptance', 'needs_for_others', 'risks'] }
phase('Fix')
const fixed = await agent(`Repository: /home/user/Billing-Optimizer (branch claude/ecstatic-fermi-orfcgo; do not commit, push or reset). Scratchpad S = ${S}.
Read ${W}/PLAN.md (the owner's words, NON-NEGOTIABLES, the revised schedule, release gates, CPU rule, needs sweep and orchestrator
decisions at the end). PHI stays in sqlite files under ${W}/${U}/; everything you print, return or write as text is aggregate or
uses RemitWatch finding ids. Run scripts that read real data with python3 -I. Other agents edit other files at the same time:
never edit a file you do not own; ask in ${W}/needs_${U}.md.
YOUR ROLE: fixer for unit ${U} (${TITLE}); you own the unit's files (its brief: ${BRIEF}). The unit's report: ${REPORT}
The independent reviewers' findings (check each yourself first; fix every high and medium that holds up and the small safe lows;
say why any is wrong or not fixed): ${JSON.stringify(FINDINGS).slice(0, 60000)}
Each fix gets a failing test first (invented data), then break and restore. Re-run the acceptance the fixes touch (world copies in
${W}/${U}/fix/, deleted at the end). At the end run the full suite ONCE in ${S}/fix/venv2_compat312/bin/python and
${S}/fix/venv_sqlite340/bin/python (serialize-only failures expected there; docs failures in tests/test_docs.py are unit F's).
Update ${W}/needs_${U}.md. Return the schema (it is saved as report_${U}.md).`, { label: `fix:${U}`, phase: 'Fix', schema: OUT })
return { unit: U, fixed }
