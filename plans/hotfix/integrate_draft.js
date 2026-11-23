export const meta = {
  name: 'hotfix2-integrated-check',
  description: 'Check the merged live hotfix (2, 2b, 2c, 2d) before it goes to main: full suite in four environments, an integrated irrational-human re-check on both hosts, a merge and live-store review; one fix round',
  phases: [
    { title: 'Check', detail: 'suite in four environments, integrated chaos re-check, merge and live-store review' },
    { title: 'Fix', detail: 'fix what the checks confirmed in the merged worktree' },
  ],
}
const S = '/tmp/claude-0/-home-user-Billing-Optimizer/96e81489-2ad2-5c70-991d-e00db2e22749/scratchpad'
const RC = args.rc            // a clean worktree of the merged commit, read-only for checkers
const WT = args.wt            // the merged branch's worktree, where a fixer may edit
const COMMIT = args.commit
const BRIEFS = `${S}/hotfix2/brief.md, ${S}/hotfix2/brief_2b.md, ${S}/hotfix2c/brief_2c.md, ${S}/hotfix2d/brief_2d.md`
const FIND = { type: 'object', properties: { summary: { type: 'string' }, findings: { type: 'array', items: { type: 'object', properties: {
  id: { type: 'string' }, severity: { type: 'string', enum: ['high', 'medium', 'low'] }, title: { type: 'string' }, evidence: { type: 'string' },
  fix: { type: 'string' } }, required: ['id', 'severity', 'title', 'evidence'] } } }, required: ['summary', 'findings'] }
const RULES = `The code under test is the merged live hotfix at ${RC} (commit ${COMMIT}, read-only: never edit it, never edit
/home/user/Billing-Optimizer or ${WT}, never commit or push). It merges four hotfixes built separately from main 949c4db; their briefs:
${BRIEFS}. The live sites run 949c4db today. Rules: CLAUDE.md; invented data in tests; real data only on copies under your own
folder (one copy of ${S}/live_state_1122/store.sqlite at a time; check df -h / first, stop under 3 GB); no real faxes, emails,
Dropbox, TriZetto or AI calls; everything you return is aggregate or uses finding ids.`
phase('Check')
const checks = await parallel([
  () => agent(`${RULES}
YOUR ROLE: run the full test suite on ${RC}, one environment at a time (the machine is shared): python3 -m pytest -q;
${S}/fix/venv2_compat/bin/python -m pytest -q (newest libraries); ${S}/fix/venv2_compat312/bin/python -m pytest -q (3.12, SQLite 3.45);
${S}/fix/venv_sqlite340/bin/python -m pytest -q (3.11.1, SQLite 3.40.0: only tests that serialize a store image may fail there); then
node tests/calc_model_check.js and node tests/lab_review_check.js. Run from ${RC} with PYTHONPATH=${RC}. For every failure: the test, the
error, and whether it is caused by the merge (the same test passes on its own hotfix branch's worktree: ${S}/hotfix2/wt, ${S}/hotfix2b/wt,
${S}/hotfix2c/wt, ${S}/hotfix2d/wt are the pre-merge trees, possibly with later edits; compare with care), flaky under load (passes when run
alone twice), or failing on main too (the checkout ${S}/chaos/rc0). Work in ${S}/hotfix2/integrated/suite/. Return findings (ids s-1, ...).`,
    { label: 'check:suite', phase: 'Check', schema: FIND }),
  () => agent(`${RULES} Also read ${S}/wire/brief_CHAOS.md (the mindset).
YOUR ROLE: integrated re-check. The four hotfixes were each re-checked alone; now they are together. Replay, on ${RC} in a real browser
and over the API on both hosts (server: uvicorn on a free localhost port with PYTHONPATH=${RC}; serverless: tests/test_cloud.py's
fixtures), the steps of the findings each hotfix fixed (${S}/chaos/r0_results/chaos_r0_*.json for the ids in each brief; ${S}/guard/L2/find.json
for L2-2, L2-3, L2-19), and then try to break the combination as an irrational human: a stray 835 and a future-dated one together with the
new swing alarm; a deadline today at 19:30 Eastern while a fax is in flight; a heir item whose appeal is followed up while the feed is stale;
a Paid amount typed oddly on a heir; a cut-short copy of a remittance next to a different remittance with the same trace. Work in
${S}/hotfix2/integrated/chaos/. Return findings only for what still fails or what the combination broke (ids ic-1, ...).`,
    { label: 'check:chaos', phase: 'Check', schema: FIND }),
  () => agent(`${RULES}
YOUR ROLE: merge and live-store review. (1) Read the merged diff against main (cd ${RC} && git diff 949c4db) for merge errors: logic
applied twice, a hunk lost, two rules-version bumps where one is meant, conflicting edits to the same function. (2) On one copy of the
live store, run the server cycle's detection and alerts with main's code (${S}/chaos/rc0) and then with the merged code: items by signal
and state before and after, their dollars, how many reopen or close and why (by the hotfix rule that moved them), the feed date before and
after, and whether the swing alarm fires on the deploy's own re-detection (it must say the rules changed, as info, not a high alarm). (3)
Check the serverless path the same way on a copy of the Vercel image (cloud.py run with a LocalState folder), and that a missing store
image is refused. Work in ${S}/hotfix2/integrated/review/. Return findings (ids mr-1, ...) and put the before and after table in the
summary.`, { label: 'check:merge', phase: 'Check', schema: FIND }),
])
const open = checks.filter(Boolean).flatMap(c => c.findings || []).filter(f => f.severity !== 'low')
if (!open.length) return { checks, fixed: null }
phase('Fix')
const fixed = await agent(`YOUR ROLE: fixer of the merged live hotfix, in ${WT} only (never commit or push; the orchestrator does).
Read ${BRIEFS}. The checks found: ${JSON.stringify(open).slice(0, 40000)}
For each: confirm on the code; if real, a failing test first (invented data), the fix, break-and-restore; if not real, say why with
evidence. Then the suites of the files you touched in python3 and the full suite once in ${S}/fix/venv2_compat312/bin/python. Return what
you fixed, the tests, and the files changed.`, { label: 'fix:integrated', phase: 'Fix' })
return { checks, fixed }
