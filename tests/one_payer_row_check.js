// Review e-3 (invented data): a Work row filtered to one payer (desk.js rowHtml with onePayer) still says who set the
// item's status ("by <name>", ui-words) and still carries the can't-be-checked mark of a payer whose remittances stopped
// (CRITIQUE_biller change 13). Usage: node one_payer_row_check.js <static dir>. Exit 1 when either is lost.
const fs = require('fs'), vm = require('vm'), path = require('path');
const read = f => fs.readFileSync(path.join(process.argv[2], f), 'utf8');
const html = read('index.html');
const grab = (src, re) => { const m = src.match(re); if (!m) throw new Error('not found: ' + re); return m[0]; };
const helpers = [/const money = [^\n]*\n/, /const money2 = [^\n]*\n/, /const esc = [^\n]*\n/, /const SIGNAL_HELP = \{[\s\S]*?\n\};/].map(re => grab(html, re)).join('\n')
  + '\n' + grab(read('faxes.js'), /const FAX_KINDS = [^\n]*\n/);
const ctx = { console, state: {}, api: async () => ({}), render: async () => {}, openDetail: () => {}, closeDetail: () => {} };
vm.createContext(ctx);
for (const src of [helpers, read('features.js'), read('today.js'), read('desk.js')]) vm.runInContext(src, ctx);
const BRIEF = { today: '2026-11-22', work_types: [], claim_kinds: {}, labels: { DENIED_AUTH: { label: 'Denied: no authorization', work: 'auth' } } };
const r = { finding_id: 'd2', signal: 'DENIED_AUTH', actionable: 'appeal', status: 'appealed', payer_group: 'Plan A', appeal_deadline: '2027-03-01',
            shortfall_cents: 11760, appealed_at: '2026-11-20T15:00:00+00:00', worked_by: 'Quorvalda', worked_to: 'appealed', worked_at: '2026-11-20T15:00:00+00:00' };
const quiet = { 'Plan A': '2026-09-01' };
const all = ctx.rowHtml(BRIEF, r, { quiet });
const one = ctx.rowHtml(BRIEF, r, { quiet, onePayer: true });
const out = { all_by: /by Quorvalda/.test(all), all_blind: /wk-blind/.test(all), one_by: /by Quorvalda/.test(one), one_blind: /wk-blind/.test(one) };
console.log(JSON.stringify(out));
process.exit(out.one_by && out.one_blind ? 0 : 1);
