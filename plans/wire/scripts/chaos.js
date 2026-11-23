export const meta = {
  name: 'chaos-irrational-humans',
  description: 'Chaos testing: testers who behave like irrational, rushed, confused humans drive the real app (both hosts) and the API; each finding is then independently reproduced or refuted',
  phases: [
    { title: 'Chaos', detail: 'persona testers drive the app and the API' },
    { title: 'Verify', detail: 'an independent verifier reproduces or refutes each finding' },
  ],
}
const S = '/tmp/claude-0/-home-user-Billing-Optimizer/96e81489-2ad2-5c70-991d-e00db2e22749/scratchpad'
const CODE = args.code            // the frozen worktree under test (read-only for testers)
const ROUND = args.round
const PERSONAS = args.personas    // [{key, who}]
const FOCUS = args.focus || ''
const FIND = { type: 'object', properties: {
  summary: { type: 'string' },
  findings: { type: 'array', items: { type: 'object', properties: {
    id: { type: 'string' }, persona: { type: 'string' }, severity: { type: 'string', enum: ['high', 'medium', 'low'] },
    harm: { type: 'string' }, title: { type: 'string' }, steps: { type: 'string' }, expected: { type: 'string' }, got: { type: 'string' },
    evidence: { type: 'string' }, file: { type: 'string' }, fix: { type: 'string' } },
    required: ['id', 'persona', 'severity', 'harm', 'title', 'steps', 'expected', 'got', 'evidence'] } },
  confusion_points: { type: 'array', items: { type: 'string' } },
}, required: ['summary', 'findings'] }
const VER = { type: 'object', properties: { verdicts: { type: 'array', items: { type: 'object', properties: {
  id: { type: 'string' }, reproduced: { type: 'boolean' }, real_harm: { type: 'boolean' }, severity: { type: 'string', enum: ['high', 'medium', 'low', 'none'] },
  why: { type: 'string' }, root_cause: { type: 'string' }, live_now: { type: 'boolean' } },
  required: ['id', 'reproduced', 'real_harm', 'severity', 'why'] } } }, required: ['verdicts'] }
const COMMON = `The code under test is the frozen checkout ${CODE} (read-only: never edit it, never edit /home/user/Billing-Optimizer, never
commit). Read ${S}/wire/brief_CHAOS.md in full first: it is your mindset, your personas and your rules (no real faxes, emails,
Dropbox, TriZetto or AI calls: unset those settings and use the fakes the tests use; real data only on copies under your own folder;
screenshots with names off or on invented data; everything you return is aggregate or uses RemitWatch finding ids; delete your
copies at the end). Useful starting points: tests/test_fax.py (the 'site' fixture: an app with a fake fax line), tests/test_cloud.py
(the serverless site fixture), tests/fakes.py, docs/OPERATIONS.md, README.md. Real data you may copy: ${S}/live_state_1122/store.sqlite
(Vercel's real store image with the billers' real statuses; open copies only). Run the server with uvicorn on a free localhost port
from the frozen checkout (PYTHONPATH=${CODE}), set REMITWATCH_PASSWORD to a long invented value and sign in with it. ${FOCUS}`
phase('Chaos')
const results = await pipeline(PERSONAS,
  p => agent(`${COMMON}
YOUR ROLE: chaos tester ${p.key} for round ${ROUND}. Play this persona fully, and invent more behaviours in its spirit: ${p.who}
Work in ${S}/chaos/r${ROUND}_${p.key}/. Drive the real app in Chromium with Playwright (both desktop 1400 px and phone 390 px where it
matters) and the API directly, on both hosts where the feature exists. Spend your effort on behaviours a real tired, rushed,
confused or suspicious person would do, not on reading code; read code only to explain what you saw. Every finding needs exact
numbered steps a verifier can repeat. Return the schema (findings with ids ${ROUND}${p.key}-1, -2, ...).`, { label: `chaos:r${ROUND}:${p.key}`, phase: 'Chaos', schema: FIND }),
  (found, p) => (found && (found.findings || []).length) ? agent(`${COMMON}
YOUR ROLE: independent verifier of chaos tester ${p.key}'s findings (round ${ROUND}). Do not edit any repository file. For EACH finding,
repeat its steps yourself on your own copy in ${S}/chaos/r${ROUND}_${p.key}_verify/ and decide: reproduced or not; whether the harm is
real (money, a wrong or duplicate fax, hidden or lost work, PHI, a broken host, trust, or a confusion a newcomer would really hit);
the severity you would give; the root cause in the code (file and function) if you can find it; and live_now = whether it affects the
code running on the live sites today (this checkout is that code). Default to not reproduced if you cannot make it happen.
The findings: ${JSON.stringify(found.findings).slice(0, 40000)}
Return the schema.`, { label: `verify:r${ROUND}:${p.key}`, phase: 'Verify', schema: VER }).then(v => ({ persona: p.key, found, verdicts: v })) : ({ persona: p.key, found, verdicts: null }),
)
return { round: ROUND, results }
