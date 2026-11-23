export const meta = {
  name: 'int-logic-integration',
  description: 'Push 2 INT: wire the archive rules, finish the states, fix the ledger pairing and the practice-manager mismatches, measure both gates and serverless memory on the integrated real world; two independent reviews; fix',
  phases: [
    { title: 'Build', detail: 'logic integration and measurement' },
    { title: 'Review', detail: 'money truth re-derived from raw rows; both hosts and memory' },
    { title: 'Fix', detail: 'the fixer checks and fixes the findings, re-measures' },
  ],
}
const S = '/tmp/claude-0/-home-user-Billing-Optimizer/96e81489-2ad2-5c70-991d-e00db2e22749/scratchpad'
const W = `${S}/wire`
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
Read ${W}/PLAN.md (the owner's words, NON-NEGOTIABLES, the revised schedule, release gates, CPU rule and needs sweep at the end).
PHI stays in sqlite files under ${W}/INT/; everything you print, return or write as text is aggregate or uses RemitWatch finding
ids. Run scripts that read real data with python3 -I. Other agents edit other files at the same time (A1's fixer, C1, C2, R, the
3a module units E, G, H): never edit a file you do not own; ask in ${W}/needs_INT.md.`
phase('Build')
const built = await agent(`${COMMON}
YOUR ROLE: unit INT, logic integration. Your brief: ${W}/brief_INT.md (read it in full, then every ${W}/report_*.md and needs_*.md;
the verified practice-manager truth is ${S}/pm/worksheet.json; the answer keys are in ${S}/truth/). Do everything in the brief,
measure as it says, write ${W}/needs_INT.md, delete your world at the end, and return the schema.`, { label: 'build:INT', phase: 'Build', schema: OUT })
phase('Review')
const lenses = [
  `money truth: take stratified samples of the integrated world's items (every reclassification and closure kind, every new signal, WORK NOW, WAITING, DONE, EXPIRED; at least 60 items) and re-derive each verdict, state and dollar size by hand from the raw rows (835 claims, lines, CAS, the EMA Production and A/R rows, the weekly exports, the billers' statuses and events); then the answer keys: run ${S}/truth/HOW_TO_RUN.md's confusion step (or the g1_final/g2_final scripts if it is not frozen yet) yourself on the rebuilt store and check INT's gate numbers; every item RemitWatch says to work that was already worked or paid, and every recoverable line it misses or calls resolved, is a finding with its finding id or line key class and dollars`,
  `both hosts and limits: the server store and the compact serverless image give identical items (every compared field) on the integrated world; the serverless detection's peak memory on Vercel's real image (${S}/live_state_1122/store.sqlite copy, reports ingested) under 1.75 GB and its time under 180 s, measured yourself with getrusage; SQL valid on SQLite 3.40 (no newer functions) and 3.45 (no outer table named in a subquery ORDER BY); a second run changes nothing; a person's status, notes, faxes and hide rules survive; every automatic closure and reclassification carries its rule, version and proof rows; no silent except`,
]
const reviews = await parallel(lenses.map((lens, i) => () => agent(`${COMMON}
YOUR ROLE: independent reviewer ${i + 1} of unit INT (logic integration). Do not edit repository files. Read ${W}/brief_INT.md,
${W}/needs_INT.md and the code INT changed. INT's report: ${JSON.stringify(built).slice(0, 14000)}
Lens: ${lens}. Build your own world in ${W}/INT/review${i + 1}/ (python3 -I -B ${S}/build/ema_checks/accept/b0_build.py <dir>; delete it
at the end). For each problem: a failing test with invented data in a scratch copy (never in the repository) or the exact
measurement that proves it; file, title, evidence, severity and the fix. Real problems only; say plainly what holds up.
Return the schema.`, { label: `review:INT:${i + 1}`, phase: 'Review', schema: FIND })))
phase('Fix')
const findings = reviews.filter(Boolean).flatMap(r => r.findings || [])
const fixed = await agent(`${COMMON}
YOUR ROLE: fixer for unit INT; you own INT's files (${W}/brief_INT.md). INT's report: ${JSON.stringify(built).slice(0, 10000)}
The reviewers' findings (check each yourself first; fix every high and medium that holds up and the small safe lows; say why any
is not fixed): ${JSON.stringify(findings).slice(0, 18000)}
Each fix gets a failing test first, then break and restore. Re-measure the gates, the 66 cases and the serverless memory after
the fixes (world in ${W}/INT/fix/, deleted at the end). At the end run the full suite ONCE in ${S}/fix/venv2_compat312/bin/python and
${S}/fix/venv_sqlite340/bin/python (serialize-only failures expected there). Update ${W}/needs_INT.md. Return the schema (it is
saved as report_INT.md).`, { label: 'fix:INT', phase: 'Fix', schema: OUT })
return { built, findings_count: findings.length, fixed }
