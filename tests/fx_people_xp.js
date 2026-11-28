// Fix wave F5 (tests/test_fx_people.py), people-1 and people-6 in the page logic alone: push 2's "Log what happened" box
// (features.js xpOutcome), its "Decide what to do" box (xpDecide) and the next step's buttons (xpBind), run in a node vm
// with a fake panel, a scripted prompt and a status that answers after a short wait (the network). Invented values only.
// Usage: node fx_people_xp.js <static dir>. Prints one JSON line.
const fs = require('fs'), vm = require('vm'), path = require('path');
const dir = process.argv[2];
const read = f => fs.readFileSync(path.join(dir, f), 'utf8');
const html = read('index.html');
const grab = (src, re) => { const m = src.match(re); if (!m) throw new Error('not found in index.html: ' + re); return m[0]; };
// the page's own helpers the panel uses: money, esc, and the walk guard (a panel "open the next" just opened ignores clicks)
const helpers = [/const money = [^\n]*\n/, /const money2 = [^\n]*\n/, /const esc = [^\n]*\n/, /const WALK_GUARD_MS = [^\n]*\n/,
  /const walkGuarded = [^\n]*\n/].map(re => grab(html, re)).join('\n');
const asked = [];
let answers = [];
const copied = [];
let eventAnswer = null;
const ctx = { console, setTimeout, Promise, Date, state: { brief: { today: '2026-11-26', labels: {}, work_types: [] }, worklist: [], filt: {} },
  $: () => null, navigator: { clipboard: { writeText: async t => { copied.push(t); } } },
  prompt: (msg, def) => { asked.push({ msg, def }); if (!answers.length) return null; const a = answers.shift(); return a === 'DEFAULT' ? def : a; },
  confirm: () => true,
  // the page's api(): a line in the item's history answered as the test says (a 409 changed for an old id); the rest is fine
  api: async (p, opts) => {
    if (/\/event$/.test(p) && eventAnswer) { const e = new Error(eventAnswer.detail); e.status = eventAnswer.status; e.code = eventAnswer.code; e.body = eventAnswer; throw e; }
    return { ok: true };
  } };
vm.createContext(ctx);
for (const src of [helpers, read('features.js')]) vm.runInContext(src, ctx);

function panel(what, note, name) {
  const els = { [`#${name}Save`]: {}, [`#${name}Cancel`]: {}, [`#${name}Note`]: { value: note || '' }, [`input[name=${name}]:checked`]: { value: what } };
  const box = { className: '', set innerHTML(v) { this._html = v; }, get innerHTML() { return this._html; }, querySelector: q => els[q] || null };
  const errs = [];
  const d = { querySelector: q => (q === '#xpMsg' ? box : (q === '#xpErr' ? { set textContent(v) { errs.push(v); } } : null)) };
  return { d, save: els[`#${name}Save`], errs };
}
// the call or portal check (features.js xpOutcome, unit ui-panel): what the payer said as a chip, the amount typed in the
// panel's own field (no browser box), the reference and the note; its Save
function callPanel(note) {
  const els = { '#callSave': {}, '#callCancel': {}, '#callForm': {}, '#callNote': { value: note || '' }, '#callRef': { value: '' }, '#callAmt': { value: '' }, '#callCode': { value: '' } };
  const chips = ['in_process', 'paid', 'denied', 'no_claim', 'records', 'wrong_payer'].map(k => ({ dataset: { ans: k }, setAttribute() {} }));
  const box = { className: '', set innerHTML(v) { this._html = v; }, get innerHTML() { return this._html; },
    querySelector: q => els[q] || null, querySelectorAll: q => (q === '[data-ans]' ? chips : []) };
  const errs = [];
  const d = { querySelector: q => (q === '#xpMsg' ? box : (q === '#xpErr' ? { set textContent(v) { errs.push(v); } } : null)) };
  return { d, box, els, chips, errs };
}
const ITEM = { finding_id: 'f-invented', status: 'appealed', shortfall_cents: 3460, charge_cents: 22365, signal: 'UNDERPAID_VS_OWN_RATE' };

// typed: the amounts typed one after another, Save pressed after each ('DEFAULT': the field left as it starts); clicks: Save
// pressed that many times at once (a double click) with the first amount typed
async function outcome(what, note, typed, clicks, item) {
  const calls = [];
  // the panel's status(): the POST with what the panel showed, then the drawer is drawn again: a short wait for the answer
  const status = (st, notes, extra) => new Promise(ok => setTimeout(() => { calls.push(Object.assign({ status: st, notes }, extra || {})); ok(); }, 30));
  const p = callPanel(note);
  ctx.xpOutcome(Object.assign({}, ITEM, item || {}), p.d, status, m => p.errs.push(m));
  const chip = p.chips.find(c => c.dataset.ans === (what === 'working' ? 'in_process' : what));
  if (chip) chip.onclick();
  const html = p.box.innerHTML || '';
  const hint = (html.match(/<span class="hint">([\s\S]*?)<\/span>/) || [])[1] || '';
  const start = (html.match(/<input id="callAmt"[^>]*>/) || [''])[0];
  const amounts = (typed || []).slice(), save = p.els['#callSave'];
  if ((clicks || 1) > 1) {
    if (amounts.length && amounts[0] !== 'DEFAULT') p.els['#callAmt'].value = amounts[0];
    const runs = [];
    for (let i = 0; i < clicks; i++) runs.push(Promise.resolve(save.onclick && save.onclick()));     // a double click: two clicks before the first answer
    await Promise.all(runs);
  } else {
    for (const a of (amounts.length ? amounts : [undefined])) { if (a !== undefined && a !== 'DEFAULT') p.els['#callAmt'].value = a; await (save.onclick && save.onclick()); }
  }
  return { hint: hint.trim(), start_empty: !/\svalue=/.test(start), sent: calls, errors: p.errs };
}
async function decide(clicks) {
  const calls = [];
  const status = (st, notes) => new Promise(ok => setTimeout(() => { calls.push({ status: st, notes }); ok(); }, 30));
  const { d, save } = panel('fixed', 'Corrected the modifier (invented)', 'xpd');
  ctx.xpDecide({ finding_id: 'f-invented-2', status: 'open', shortfall_cents: 3460 }, d, status, () => {}, {});
  const runs = [];
  for (let i = 0; i < clicks; i++) runs.push(Promise.resolve(save.onclick && save.onclick()));
  await Promise.all(runs);
  return { sent: calls };
}

(async () => {
  const out = {};
  out.paid_typed_as_ema_prints_it = await outcome('paid', '', ['1,250.00']);
  out.paid_ok_on_what_the_prompt_holds = await outcome('paid', '', ['DEFAULT']);
  out.paid_typed_with_dollar_sign = await outcome('paid', '', ['$1,250.00']);
  out.paid_typed_cents = await outcome('paid', '', ['1250.5']);
  out.paid_typed_bad_then_cancel = await outcome('paid', '', ['12.345', '0', 'abc']);
  // a line the run found paid before it was listed (appeal_block 'paid'): its step says close it as Paid with $0.00
  out.paid_zero_on_a_paid_line = await outcome('paid', '', ['0'], 1, { appeal_block: 'paid', status: 'open', work_state: 'done' });
  out.working_double_click = await outcome('working', 'Called the payer: in review (invented ref 0001)', undefined, 2);
  out.paid_double_click = await outcome('paid', '', ['1,250.00', '1,250.00'], 2);
  out.decide_double_click = await decide(2);
  ctx.state.walkUntil = Date.now() + 60000;              // a panel the walk opened a moment ago
  out.save_while_walk_guarded = await outcome('working', 'Called the payer (invented)', undefined, 1);
  out.decide_while_walk_guarded = await decide(1);
  ctx.state.walkUntil = 0;
  // Copy the steps: the clipboard takes them; the line in the item's history is refused (a panel drawn before a run gave
  // the item a new number): the panel says so
  {
    eventAnswer = { status: 409, code: 'changed', heir: 'f-heir', detail: 'This item has a new number since your page loaded: open it again from the list.' };
    const errs = [], said = [];
    const btn = { dataset: { xp: 'copy_instructions' } };
    const d = { querySelector: q => (q === '#xpErr' ? { set textContent(v) { if (v) errs.push(v); } } :
                                      q === '#xpMsg' ? { set className(v) {}, set textContent(v) { said.push(v); } } : null),
                querySelectorAll: q => (q === '[data-xp]' ? [btn] : []) };
    const x = { explain: { next_step: { route: 'call', buttons: ['copy_instructions'], instructions: 'Call the payer (invented steps).', steps: [] },
                           verdict: {}, state: {} } };
    ctx.xpBind(Object.assign({}, ITEM, { status: 'open' }), x, { packets: [] }, d, () => {});
    copied.length = 0;
    await btn.onclick();
    await new Promise(ok => setTimeout(ok, 50));
    out.copy_steps_history_refused = { copied: copied.slice(), said, errors: errs };
    eventAnswer = null;
  }
  out.reader = Object.fromEntries(['1,250.00', '$1,250.00', '12.345', '34.60', '', '0'].map(t => [t, ctx.paidCents(t, 22365)]));
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
