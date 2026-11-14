/* ==================================================================== Collections calculator
   One clinician's year (or the practice's average Mohs surgeon, general dermatologist or APP): what the schedule brings
   in at each payer's own rates, what next year's Medicare rules take, what the plan changes, and what corrected
   contracts would add. The figures come from /api/activity/calculator (owner only); the arithmetic is CalcModel
   (calc_model.js), run in the page as the sliders move. */

// [key, label, min, max, step, format, group of everything else (when the slider scales one)]
const CALC_VOLUME = [
  ['Clinic time', [
    ['days_per_week', 'Clinic days a week', 0, 6, 0.1, 'n1'],
    ['weeks_per_year', 'Weeks of clinic a year', 20, 52, 1, 'n0'],
  ]],
  ['A clinic day', [
    ['mohs_cases', 'Mohs cases', 0, 30, 0.1, 'n1'],
    ['excisions', 'Excisions', 0, 15, 0.1, 'n1'],
    ['visits', 'Visits', 0, 60, 0.5, 'n1'],
    ['bx_patients', 'Patients biopsied', 0, 30, 0.5, 'n1'],
    ['ak_patients', 'Patients with AKs frozen', 0, 40, 0.5, 'n1'],
    ['benign', 'Patients with benign lesions frozen', 0, 30, 0.5, 'n1'],
    ['other_rest', 'Other procedures (shaves, ED&C, injections...), against now', 0, 3, 0.05, 'pct', 'rest'],
  ]],
];
const CALC_CLINICAL = [
  ['Mohs', [
    ['mohs_stages', 'Stages per case', 1, 4, 0.01, 'n2'],
    ['mohs_hn', 'Cases on the head, neck, hands or feet (17311)', 0, 1, 0.01, 'pct'],
    ['fg_share', 'Flaps or grafts per 100 cases', 0, 1.5, 0.01, 'per100'],
    ['lay_mohs', 'Layered closures per 100 cases', 0, 1.5, 0.01, 'per100'],
  ]],
  ['Excisions', [['lay_exc', 'Layered closures per 100 excisions', 0, 1.5, 0.01, 'per100']]],
  ['Visits', [
    ['new_share', 'New patients', 0, 1, 0.01, 'pct'],
    ['est_high', 'Established visits at 99214 or 99215', 0, 1, 0.01, 'pct'],
    ['new_high', 'New visits at 99204 or 99205', 0, 1, 0.01, 'pct'],
  ]],
  ['Lesions', [
    ['bx_per_patient', 'Biopsies per biopsied patient', 1, 6, 0.05, 'n2'],
    ['ak_per_patient', 'AKs per patient (patients under 15)', 1, 14, 0.1, 'n1'],
    ['ak_15', 'AK patients with 15 or more (17004)', 0, 1, 0.01, 'pct'],
  ]],
];
const CALC_ANCILLARY = [
  ['other_radiation', 'Radiation', 'radiation'], ['other_skin', 'Skin-substitute products', 'skin'], ['other_path', 'Pathology billed under this number', 'path'],
  ['other_drug', 'Drugs', 'drug'], ['other_nurse', 'Staff services (light, injections by staff)', 'nurse'],
];
const CALC_Q = [['commercial', 'Commercial'], ['medicare', 'Medicare (what supplements leave)'], ['medicare_advantage', 'Medicare Advantage'], ['other', 'Other payers']];
const CALC_2027 = [
  ['bx_visit_share', 'Biopsied Medicare patients also billed a visit with modifier 25', 0, 1, 0.01, 'pct'],
  ['mohs_visit_share', 'Mohs cases with a visit billed with modifier 25 the same day', 0, 1, 0.01, 'pct'],
];
const calcFmt = {n0: v => Math.round(v).toLocaleString(), n1: v => (Math.round(v * 10) / 10).toLocaleString(), n2: v => (Math.round(v * 100) / 100).toFixed(2),
                 pct: v => Math.round(v * 100) + '%', per100: v => Math.round(v * 100) + ' per 100'};
const KIND_LABEL = {medicare: 'Medicare', medicare_advantage: 'Medicare Advantage', commercial: 'Commercial', medicaid: 'Medicaid', supplement: 'Supplement', other: 'Other'};
const ROLE_LABEL = {mohs: 'Mohs surgeon', general: 'General dermatologist', apps: 'APP'};
const moneyK = c => { const v = Math.round((c || 0) / 100000) * 100000; return v < 0 ? '−' + money(-v) : money(v); };   // a year's figure, to the nearest $1,000
const roundK = c => Math.round((c || 0) / 100000) * 100000;

// a yearly dollar figure as a person types it: 1,600,000, $1,600,000, 1.6m, 1.6M, 950k, 1600k. NaN for anything else.
// Used by the target here and on the provider sheet.
function calcMoney(s){
  const t = String(s == null ? '' : s).trim().replace(/^\$\s*/, '').replace(/\s+/g, '');
  const m = /^((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)([kKmM]?)$/.exec(t);
  if(!m) return NaN;
  return +m[1].replace(/,/g, '') * (!m[2] ? 1 : /k/i.test(m[2]) ? 1e3 : 1e6);
}

// the most a slider can mean: weeks in a year, a clinic day a day of the week, 14 AKs before 17004 takes over
const CALC_CAP = {weeks_per_year: 52, days_per_week: 6, ak_per_patient: 14};
// a slider's range: wide enough for the clinician's own figure, never past what is possible. A share of patients or cases
// stops at 100%; closures per 100 cases stop at 200 (the model allows a second closure on a case, a later procedure of
// the day); the ancillary sliders are against now and keep their own range.
function calcRange(cs, def){
  const [k, , mn, mx, st, f, grp] = def, now = +cs.base[k] || 0;
  const dec = (String(st).split('.')[1] || '').length, fix = v => +(+v).toFixed(dec);
  let hi = Math.max(mx, Math.ceil(now * 1.5 / st) * st);
  if(CALC_CAP[k] != null) hi = Math.min(hi, Math.max(CALC_CAP[k], now));
  else if(f === 'pct' && !grp) hi = Math.min(hi, Math.max(1, now));
  else if(f === 'per100') hi = Math.min(hi, Math.max(2, now));
  const lo = Math.min(mn, Math.floor(now / st) * st);
  return [fix(Math.max(0, lo)), fix(hi)];
}

// folds stay as the reader left them, and the focused control keeps the focus, when a panel is drawn again
function calcFold(key, dflt){ const f = state.calc.folds = state.calc.folds || {}; return (f[key] == null ? dflt : f[key]) ? 'open' : ''; }
function calcFoldWire(box){
  box.querySelectorAll('details[data-fold]').forEach(el => el.addEventListener('toggle', () => { (state.calc.folds = state.calc.folds || {})[el.dataset.fold] = el.open; }));
}
const CALC_KEYS = ['data-calc-fix', 'data-calc-idx', 'data-calc-t', 'data-calc-year', 'data-calc-who', 'data-calc-view', 'data-num', 'data-qnum'];
function calcKeyOf(el){
  if(el.id) return '#' + CSS.escape(el.id);
  for(const at of CALC_KEYS) if(el.hasAttribute(at)) return `[${at}="${CSS.escape(el.getAttribute(at))}"]`;
  const det = el.tagName === 'SUMMARY' && el.parentElement && el.parentElement.dataset.fold;
  return det ? `details[data-fold="${CSS.escape(det)}"] > summary` : null;
}
function calcFocusKeep(root){
  const a = document.activeElement;
  if(!root || !a || a === document.body || !root.contains(a)) return () => {};
  const sel = calcKeyOf(a);
  let s0 = null, s1 = null;
  try { s0 = a.selectionStart; s1 = a.selectionEnd; } catch(e){}
  return () => {
    if(!sel) return;
    const el = root.querySelector(sel);
    if(!el || el === document.activeElement) return;
    el.focus({preventScroll: true});
    try { if(s0 != null) el.setSelectionRange(s0, s1); } catch(e){}
  };
}
function calcRedraw(box, html){ const back = calcFocusKeep(box); box.innerHTML = html; calcFoldWire(box); back(); }

// rows rounded to $1,000 so that they add up to the rounded total (largest remainders)
function calcRoundRows(values, total){
  const U = 100000, T = Math.round(total / U), fl = values.map(v => Math.floor(v / U));
  const order = values.map((v, i) => [v / U - fl[i], i]).sort((a, b) => b[0] - a[0]);
  let left = T - fl.reduce((a, b) => a + b, 0);
  for(let j = 0; left > 0 && order.length; j = (j + 1) % order.length, left--) fl[order[j][1]] += 1;
  for(let j = order.length - 1; left < 0 && order.length; j = (j - 1 + order.length) % order.length, left++) fl[order[j][1]] -= 1;
  return fl.map(v => v * U);
}

// what carries over to another clinician, another window and 'Back to the schedule now': the year and the assumptions
// (patient balances, the 2027 readings, ancillary, contracts); the levers start again from the new schedule, and so do
// the shares of biopsies and Mohs cases with a visit beside them (each clinician's own, where the figures have them)
const CALC_ASSUME = ['q', 'ma_same_day', 'cut_add_ons', 'mohs_57', 'include_ancillary', 'include_path', 'fix_mode', 'target_pct',
                     'corrected', 'targets', 'indexed'];
function calcKeep(x, d, base){
  if(!x) return {};
  const keep = {}, has = g => !!(d.payers || {})[g];
  for(const k of CALC_ASSUME) if(x[k] != null) keep[k] = JSON.parse(JSON.stringify(x[k]));
  if(x.year === d.rate_year || (d.next_year && x.year === d.next_year)) keep.year = x.year;
  for(const k of ['corrected', 'targets']) if(keep[k]) keep[k] = Object.fromEntries(Object.entries(keep[k]).filter(([g]) => has(g)));
  if(keep.indexed) keep.indexed = Object.assign({}, base.indexed, Object.fromEntries(Object.entries(keep.indexed).filter(([g]) => has(g))));
  return keep;
}
// strategies that stop applying (another clinician's work, or 2027-only on this year's rates) are unticked, not left
// ticked and greyed out
function calcLabFit(cs){
  const chosen = cs.lab && cs.lab.chosen;
  if(!chosen || typeof LabModel === 'undefined' || !cs.data || !cs.x) return;
  for(const [id, c] of Object.entries(chosen)){
    const S = LabModel.STRATEGIES.find(s => s.id === id);
    if(c && c.on && S && ((S.only === 'next' && cs.x.year !== cs.data.next_year) || (S.applies && !S.applies(cs.data, cs.x)))) c.on = false;
  }
}

// moving a slider or ticking a box is use: the owner's idle lock waits (at most one restart of its timer every 15 seconds)
const calcIdleWired = new WeakSet();
let calcIdleAt = 0;
function calcActive(){
  if(state.tab !== 'calculator' || !state.ownerKey || typeof pvArmIdle !== 'function') return;
  const t = Date.now(); if(t - calcIdleAt < 15000) return;
  calcIdleAt = t; pvArmIdle();
}

// narrow screens: the wide tables wrap or stack inside their panel instead of scrolling sideways
function calcStyle(){
  if(document.getElementById('calcStyle')) return;
  const s = document.createElement('style'); s.id = 'calcStyle';
  s.textContent = `table.calc-lines td:first-child,table.calc-goal td:first-child{white-space:normal}
@media(max-width:640px){
  table.calc-lines th,table.calc-lines td{padding:5px 4px}
  table.calc-lines th{white-space:normal;vertical-align:bottom}
  table.calc-goal td.num{white-space:normal}
  table.calc-lines td:first-child{font-size:12px}
  table.calc-payers thead{display:none}
  table.calc-payers,table.calc-payers tbody{display:block;width:100%}
  table.calc-payers tr{display:grid;grid-template-columns:26px repeat(3,minmax(0,1fr));gap:2px 6px;border-bottom:1px solid var(--line);padding:6px 0}
  table.calc-payers td{display:block;border:0;padding:0;min-width:0}
  table.calc-payers td.c-payer{grid-column:2 / -1;overflow-wrap:anywhere}
  table.calc-payers td.c-share{display:none}
  table.calc-payers td.c-pays{grid-column:2}table.calc-payers td.c-target{grid-column:3}table.calc-payers td.c-up{grid-column:4}
  table.calc-payers td.num{text-align:left;white-space:normal}
  table.calc-payers td.num[data-label]::before{content:attr(data-label);display:block;font-size:11px;font-weight:400;color:var(--muted)}
  table.calc-payers td.c-target .calc-num{width:64px}
  table.calc-cards thead{display:none}
  table.calc-cards,table.calc-cards tbody{display:block;width:100%}
  table.calc-cards tr{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:2px 10px;border-bottom:1px solid var(--line);padding:6px 0}
  table.calc-cards td{display:block;border:0;padding:0;min-width:0;text-align:left;white-space:normal}
  table.calc-cards td:first-child{grid-column:1 / -1;font-weight:600}
  table.calc-cards td[data-label]::before{content:attr(data-label);display:block;font-size:11px;font-weight:400;color:var(--muted)}
}`;
  document.head.appendChild(s);
}

async function calculatorTab(v){
  const cs = state.calc = Object.assign({provider: 'mohs', months: 6, data: null, x: null, cal: null, target: '', view: 'calc'}, state.calc || {});
  state.pv = state.pv || {};
  cs.el = v;
  calcStyle();
  if(!calcIdleWired.has(v)){ calcIdleWired.add(v); ['input', 'change', 'click'].forEach(t => v.addEventListener(t, calcActive, true)); }
  if(pvLocked() && !state.ownerKey){ pvUnlockForm(v); return; }
  const key = cs.provider + '|' + cs.months;
  if(!cs.data || cs.data._for !== key){
    v.classList.add('busy');
    try {
      const d = await api('/api/activity/calculator?' + qs({provider: cs.provider, months: cs.months}));
      if(state.tab !== 'calculator') return;
      if(!d.ready){ v.classList.remove('busy'); v.innerHTML = `<div class="panel"><h2>Collections calculator</h2><div class="note">${esc(d.message || 'The figures are not built yet.')}</div></div>`; return; }
      d._for = key;
      // another clinician or another window: the year and the assumptions carry over, the levers start from the new
      // schedule; a target typed for one clinician is not left standing for the next
      const before = cs.data && cs.data.who ? cs.data.who.id : null;
      if(before && before !== d.who.id){ cs.target = ''; cs.sheetTarget = ''; }
      const base = CalcModel.defaults(d), keep = calcKeep(cs.x, d, base);
      cs.data = d; cs.cal = CalcModel.calibrate(d); cs.base = base; cs.x = Object.assign(JSON.parse(JSON.stringify(base)), keep);
      calcLabFit(cs);
    } catch(e){
      if(pvLocked() && [403, 429, 503].includes(e.status)){ pvForget(); pvUnlockForm(v, e.message); return; }
      v.classList.remove('busy'); v.innerHTML = `<div class="panel"><h2>Collections calculator</h2><div class="note">${esc(e.message)}</div></div>`; return;
    }
  }
  if(state.ownerKey) pvArmIdle();
  v.classList.remove('busy');
  calcDraw(v);
}

function calcWho(d){
  const w = d.who || {};
  if(ROLE_LABEL[w.id]) return `${w.name}: ${w.clinicians} clinician${w.clinicians===1?'':'s'} (${(w.members||[]).join('; ')}), their clinic days pooled`;
  return w.name || 'Clinician';
}

function calcDraw(v){
  const cs = state.calc, d = cs.data, x = cs.x;
  const back = calcFocusKeep(v);
  const people = (d.clinicians || []);
  const opt = (val, label) => `<option value="${esc(val)}" ${cs.provider === val ? 'selected' : ''}>${esc(label)}</option>`;
  const roles = Object.entries(d.roles || {}).filter(([, n]) => n > 0);
  const y27 = d.next_year;
  if(ROLE_LABEL[d.who.id] && !d.who.clinicians){
    v.innerHTML = `<div class="panel"><h2>Collections calculator</h2><div class="note">No ${esc(ROLE_LABEL[d.who.id].toLowerCase())} held clinic on enough days in ${esc(d.from)} to ${esc(d.to)} to make an average. Pick another.</div>
      <div class="segs">${roles.map(([r]) => `<button class="seg" data-calc-who="${r}">${esc(ROLE_LABEL[r])} (average)</button>`).join('')}</div></div>`;
    v.querySelectorAll('[data-calc-who]').forEach(b => b.onclick = () => { cs.provider = b.dataset.calcWho; render(); });
    return;
  }
  // the provider sheet is for one clinician: no other clinician's name on screen while it shows
  const sheet = cs.view === 'sheet';
  const pick = sheet
    ? `<span class="small"><b>${esc(d.who.name || 'Clinician')}</b></span> <button id="calcPick" class="ghost">Pick another clinician</button>`
    : `<div class="segs" role="group" aria-label="whose year">
        ${roles.map(([r]) => `<button class="seg ${cs.provider===r?'on':''}" data-calc-who="${r}">${esc(ROLE_LABEL[r])} (average)</button>`).join('')}
      </div>
      <select id="calcWho" aria-label="clinician">
        <option value="">One clinician…</option>
        <optgroup label="Physicians">${people.filter(c=>c.tier==='physician').map(c=>opt(c.provider_id, c.name)).join('')}</optgroup>
        <optgroup label="APPs">${people.filter(c=>c.tier==='nonphysician').map(c=>opt(c.provider_id, c.name)).join('')}</optgroup>
      </select>`;
  v.innerHTML = `
  <div class="panel">
    <h2 style="margin-bottom:4px">Collections calculator</h2>
    <div class="small muted">${esc(sheet ? (d.who.name || 'Clinician') : calcWho(d))}. Built from services in ${esc(d.from)} to ${esc(d.to)} (${(d.schedule.clinic_days||0).toLocaleString()} clinic days of 5 or more patients), each payer at its own rates.</div>
    <div class="filters" style="margin-top:8px;gap:8px;flex-wrap:wrap">
      ${pick}
      <select id="calcMonths" aria-label="months read">
        <option value="6" ${cs.months==6?'selected':''}>Last 6 months (run-rate now)</option>
        <option value="12" ${cs.months==12?'selected':''}>Last 12 months</option>
      </select>
      ${y27 ? `<div class="segs" role="group" aria-label="year planned">
        <button class="seg ${x.year===y27?'on':''}" data-calc-year="${esc(y27)}">Plan ${esc(y27)}</button>
        <button class="seg ${x.year===d.rate_year?'on':''}" data-calc-year="${esc(d.rate_year)}">${esc(d.rate_year)} rates</button></div>` : ''}
      <button id="calcReset" class="ghost">Back to the schedule now</button>
    </div>
    ${calcViews(v)}
  </div>
  <div id="calcBody">
  <div class="panel" id="calcFall"></div>
  <div class="grid two">
    <div>
      <div class="panel" id="calcInputs"></div>
    </div>
    <div>
      <div class="panel" id="calcLines"></div>
      <div class="panel" id="calcPayers"></div>
      <div class="panel" id="calcMoves"></div>
      <div class="panel" id="calcGoal"></div>
      <details class="panel" id="calcHow" data-fold="how" ${calcFold('how', false)}><summary><b>How this is worked out</b></summary><div id="calcHowBody" class="small"></div></details>
    </div>
  </div>
  </div>`;
  calcFoldWire(v);
  v.querySelectorAll('[data-calc-who]').forEach(b => b.onclick = () => { cs.provider = b.dataset.calcWho; render(); });
  const who = v.querySelector('#calcWho'); if(who) who.onchange = e => { if(e.target.value){ cs.provider = e.target.value; render(); } };
  const pk = v.querySelector('#calcPick'); if(pk) pk.onclick = () => { cs.view = 'calc'; calcDraw(v); };
  v.querySelector('#calcMonths').onchange = e => { cs.months = +e.target.value; render(); };
  v.querySelectorAll('[data-calc-year]').forEach(b => b.onclick = () => { x.year = b.dataset.calcYear; calcLabFit(cs); calcDraw(v); });
  // the levers go back to the schedule as it is; the year and the assumptions stay
  v.querySelector('#calcReset').onclick = () => { cs.x = Object.assign(JSON.parse(JSON.stringify(cs.base)), calcKeep(x, d, cs.base)); calcLabFit(cs); calcDraw(v); };
  v.querySelectorAll('[data-calc-view]').forEach(b => b.onclick = () => { cs.view = b.dataset.calcView; calcDraw(v); });
  back();
  if(cs.view && cs.view !== 'calc'){
    const run = {lab: labDraw, figures: figuresDraw, sheet: sheetDraw}[cs.view];
    if(run) run(v).catch(e => { const b = v.querySelector('#calcBody'); if(b) b.innerHTML = `<div class="panel"><div class="note">${esc(e.message)}</div></div>`; });
    return;
  }
  calcInputs(v);
  calcUpdate();
  v.querySelector('#calcHowBody').innerHTML = calcHow(d);
  back();
}

// Mohs sliders are not offered to a clinician who does no Mohs (and has none in the schedule now)
const CALC_MOHS = ['mohs_cases', 'mohs_stages', 'mohs_hn', 'fg_share', 'lay_mohs', 'mohs_visit_share'];
const calcMohsOk = cs => CalcModel.hasMohs(cs.data) || cs.base.mohs_cases > 0 || cs.x.mohs_cases > 0;
const calcShow = (f, v) => f === 'pct' || f === 'per100' ? Math.round(v * 100) : (+v).toFixed(f === 'n2' ? 2 : f === 'n1' ? 1 : 0);

function calcRow(cs, def, otherNow){
  const [k, label, , , st, f, grp] = def, x = cs.x;
  if(grp && !(otherNow[grp] > 0)) return '';
  if(CALC_MOHS.includes(k) && !calcMohsOk(cs)) return '';
  const now = cs.base[k], val = x[k], pctLike = f === 'pct' || f === 'per100';
  const [lo, hi] = calcRange(cs, def);
  const extra = k === 'ak_patients' ? ` · ${calcFmt.n1(CalcModel.akPatients(cs.data, x).lesions)} lesions a day`
    : k === 'bx_patients' ? ` · ${calcFmt.n1(x.bx_patients * x.bx_per_patient)} biopsies a day`
    : k === 'benign' ? ' · 17110 pays once per patient for 1 to 14 lesions'
    : grp ? ` of ${money(otherNow[grp])} allowed a clinic day` : '';
  return `<div class="calc-row" data-k="${k}">
    <label for="cr_${k}">${esc(label)}</label>
    <div class="calc-ctl"><input type="range" id="cr_${k}" min="${lo}" max="${hi}" step="${st}" value="${val}">
      <input type="number" class="calc-num" data-num="${k}" aria-label="${esc(label)}" min="${pctLike ? Math.round(lo * 100) : lo}" max="${pctLike ? Math.round(hi * 100) : hi}" step="${pctLike?1:st}" value="${calcShow(f, val)}">${f==='pct'?'<span class="small">%</span>':''}</div>
    <div class="small muted">now ${calcFmt[f](now || 0)}<span data-extra="${k}">${extra}</span></div>
  </div>`;
}

function calcInputs(v){
  const cs = state.calc, d = cs.data, x = cs.x;
  const otherNow = {};
  for (const [c, u] of Object.entries(d.levers.other.codes)) {
    const grp = (d.levers.other.groups || {})[c] || 'rest', cc = d.codes[c];
    otherNow[grp] = (otherNow[grp] || 0) + u * (cc && cc.units ? cc.allowed_cents / cc.units : 0);
  }
  const block = groups => groups.map(([title, rows]) => { const body = rows.map(r => calcRow(cs, r, otherNow)).join(''); return body ? `<h3 class="calc-h">${esc(title)}</h3>${body}` : ''; }).join('');
  const ancRows = CALC_ANCILLARY.map(([k, label, grp]) => calcRow(cs, [k, label, 0, 3, 0.05, 'pct', grp], otherNow)).join('');
  const next = x.year === d.next_year;
  const s57 = CalcModel.visit57Share(d);
  const box = v.querySelector('#calcInputs');
  calcRedraw(box, `<h2>The schedule</h2>
    <div class="small muted" style="margin-bottom:6px">"Now" is ${esc(ROLE_LABEL[d.who.id] ? 'the average' : 'this clinician')}'s own figure over the months read. These are the levers a schedule controls; goal seeking moves only these.</div>
    ${block(CALC_VOLUME)}
    <details style="margin-top:10px" data-fold="cases" ${calcFold('cases', false)}><summary><b>How cases go</b> <span class="small muted">clinical outcomes, not targets</span></summary>
      <div class="small muted" style="margin:4px 0">Stages, closures, visit levels and lesions per patient follow what each patient needs and what the note supports, and payers audit them. Use these to see what a change in case mix would do, not as goals.</div>
      ${block(CALC_CLINICAL)}</details>
    <details style="margin-top:6px" data-fold="balances" ${calcFold('balances', false)}><summary><b>Patient balances</b> <span class="small muted">share collected, by kind of payer</span></summary>
      <div class="small muted" style="margin:4px 0">The remittances show what a patient was left to owe, not what the patient paid. These shares are assumptions until the practice's patient-payment figures are loaded; the defaults are the floor the practice's early ledger exports showed. Nothing is counted from Medicaid patients. Medicare cost-sharing cannot be billed to qualified Medicare beneficiaries (dual eligibles), even when Medicaid pays nothing.</div>
      ${CALC_Q.map(([k, label]) => `<div class="calc-row" data-q="${k}"><label for="cq_${k}">${esc(label)}</label>
        <div class="calc-ctl"><input type="range" id="cq_${k}" min="0" max="1" step="0.01" value="${x.q[k]}"><input type="number" class="calc-num" data-qnum="${k}" aria-label="${esc(label)}, share collected" min="0" max="100" step="1" value="${Math.round(x.q[k]*100)}"><span class="small">%</span></div></div>`).join('')}
    </details>
    ${next ? `<details style="margin-top:6px" data-fold="rule2027" ${calcFold('rule2027', true)}><summary><b>Medicare's ${esc(d.next_year)} same-day rule</b></summary>
      ${CALC_2027.map(r => calcRow(cs, r, otherNow)).join('')}
      <label class="small" style="display:block"><input type="checkbox" id="calcMASD" ${x.ma_same_day!==false?'checked':''}> Medicare Advantage plans that move with Medicare (ticked under Contracts) take on the ${esc(d.next_year)} changes too: modifier B1 in place of G2211, and this rule. The rule binds traditional Medicare; a plan follows it only if its contract follows Medicare's payment rules.</label>
      <label class="small" style="display:block"><input type="checkbox" id="calcAdd" ${x.cut_add_ons?'checked':''}> It cuts add-on codes too (extra Mohs stages, extra biopsies, 17003). The rule says every service but the highest; CMS has not said how add-ons are handled.</label>
      ${s57 != null && calcMohsOk(cs) ? `<label class="small" style="display:block"><input type="checkbox" id="calcM57" ${x.mohs_57?'checked':''}> If CMS's edits also catch a visit with modifier 57 beside a 0-day Mohs (${Math.round(s57 * 100)} of every 100 Mohs cases here). A sensitivity, not a reading of the rule.</label>` : ''}
      <div class="small sev-medium" style="margin-top:4px">Do not change modifier 25 to 57 to avoid this rule; 57 is only for the visit that decides a 90-day procedure.</div>
    </details>` : ''}
    <details style="margin-top:6px" data-fold="ancillary" ${calcFold('ancillary', false)}><summary><b>The practice's ancillary and product revenue</b> <span class="small muted">not this clinician's production</span></summary>
      <div class="small muted" style="margin:4px 0">Radiation, skin-substitute products, pathology, drugs and staff services come to the practice and carry costs of their own. Pathology technical component, lab, radiation delivery and drugs not given incident to your own service are designated health services and may not be credited for your referrals; services you personally perform (including slides you read) and incident-to services may be. They are left out of the figures above unless you add them here.</div>
      <label class="small" style="display:block"><input type="checkbox" id="calcAnc" ${x.include_ancillary?'checked':''}> Add them to the totals</label>
      ${ancRows}
      <label class="small" style="display:block;margin-top:6px"><input type="checkbox" id="calcPath" ${x.include_path?'checked':''} ${x.include_ancillary?'':'disabled'}> Also the pathology this clinician's biopsies bring the practice (read and billed by the practice's own reader; slides this clinician reads are in the pathology line above). Counted only with the box above.</label>
    </details>`);
  const all = CALC_VOLUME.concat(CALC_CLINICAL).flatMap(g => g[1]).concat(CALC_2027).concat(CALC_ANCILLARY.map(([k, l, g]) => [k, l, 0, 3, 0.05, 'pct', g]));
  box.querySelectorAll('.calc-row[data-k]').forEach(r => {
    const k = r.dataset.k, def = all.find(s => s[0] === k), f = def[5], pct = f === 'pct' || f === 'per100';
    const [lo, hi] = calcRange(cs, def);
    const range = r.querySelector('input[type=range]'), num = r.querySelector('.calc-num');
    range.oninput = () => { x[k] = +range.value; num.value = pct ? Math.round(x[k] * 100) : range.value; calcSoon(); };
    // a typed figure is held to the slider's range; the box shows what the plan uses once the typing is done
    num.oninput = () => { const n = parseFloat(num.value); if(!isFinite(n)) return; x[k] = Math.min(hi, Math.max(lo, pct ? n / 100 : n)); range.value = x[k]; calcSoon(); };
    num.onchange = () => { num.value = calcShow(f, x[k]); };
  });
  box.querySelectorAll('.calc-row[data-q]').forEach(r => {
    const k = r.dataset.q, range = r.querySelector('input[type=range]'), num = r.querySelector('.calc-num');
    range.oninput = () => { x.q[k] = +range.value; num.value = Math.round(x.q[k] * 100); calcSoon(); };
    num.oninput = () => { const n = parseFloat(num.value); if(!isFinite(n)) return; x.q[k] = Math.min(1, Math.max(0, n / 100)); range.value = x.q[k]; calcSoon(); };
    num.onchange = () => { num.value = Math.round(x.q[k] * 100); };
  });
  const on = (id, f) => { const el = box.querySelector(id); if(el) el.onchange = e => { f(e.target.checked); calcUpdate(); }; };
  on('#calcMASD', c => x.ma_same_day = c); on('#calcAdd', c => x.cut_add_ons = c); on('#calcM57', c => x.mohs_57 = c);
  on('#calcAnc', c => { x.include_ancillary = c; const p = box.querySelector('#calcPath'); if(p) p.disabled = !c; });
  on('#calcPath', c => x.include_path = c);
}

let calcTimer = null;
function calcSoon(){ if(calcTimer) return; calcTimer = requestAnimationFrame(() => { calcTimer = null; calcUpdate(); }); }

// the same assumptions (collections, 2027 readings, contracts, ancillary) on another schedule or year
function calcWith(base, x, over){
  return Object.assign({}, base, {q: x.q, ma_same_day: x.ma_same_day, cut_add_ons: x.cut_add_ons, bx_visit_share: x.bx_visit_share,
    mohs_visit_share: x.mohs_visit_share, mohs_57: x.mohs_57, include_ancillary: x.include_ancillary, include_path: x.include_path, indexed: x.indexed,
    fix_mode: x.fix_mode, target_pct: x.target_pct, targets: x.targets, corrected: {}}, over || {});
}

function calcUpdate(){
  const cs = state.calc; if(!cs || !cs.data || !cs.el || !cs.el.isConnected) return;
  const d = cs.data, x = cs.x, v = cs.el, cal = cs.cal;
  if(!v.querySelector('#calcFall')) return;          // another view of the tab is showing
  const ev = y => CalcModel.evaluate(d, y, cal);
  const nowRate = ev(calcWith(cs.base, x, {year: d.rate_year}));
  const nowNext = x.year === d.next_year ? ev(calcWith(cs.base, x, {year: d.next_year})) : nowRate;
  const plan = ev(calcWith(x, x, {year: x.year}));
  const fixed = ev(Object.assign({}, x));
  const nCorr = Object.values(x.corrected).filter(Boolean).length;
  const sign = dv => Math.abs(dv) < 50000 ? '' : `<span class="${dv>0?'ok':'sev-high'}">${dv>0?'+':'−'}${moneyK(Math.abs(dv))}</span>`;
  // the band: patient balances and the open 2027 readings (add-on codes, Medicare Advantage plans, modifier 57)
  const qShift = s => Object.fromEntries(Object.entries(x.q).map(([k, q]) => [k, Math.min(1, Math.max(0, q + s))]));
  const s57 = CalcModel.visit57Share(d) != null && CalcModel.hasMohs(d);
  const band = (s, add, m57) => [true, false].map(ma => ev(Object.assign({}, x, {q: qShift(s), cut_add_ons: add, ma_same_day: ma, mohs_57: m57})).totalCollections);
  const lo = Math.min(fixed.totalCollections, ...band(-0.15, true, s57 || !!x.mohs_57));
  const hi = Math.max(fixed.totalCollections, ...band(0.10, false, false));
  // what payers allowed in the window for this clinician's own production, at a year's pace
  const anc = new Set(CalcModel.ANCILLARY);
  let prodActual = 0;
  for(const [c, cc] of Object.entries(d.codes)){ const g = (d.levers.other.groups || {})[c]; if(!(g && anc.has(g))) prodActual += cc.allowed_cents || 0; }
  const perYear = cs.base.days_per_week * cs.base.weeks_per_year / Math.max(1, d.schedule.clinic_days);
  // each step is the difference of the rounded running totals, so the steps add up to the total shown
  const R = [nowRate, nowNext, plan, fixed].map(r => roundK(r.totalCollections));
  const rows = [
    ['Allowed by payers for this production, read at a year\'s pace', null, prodActual * perYear, 'muted'],
    [`Collections now: the schedule as it is, ${d.rate_year} rates`, null, R[0], ''],
  ];
  if(x.year === d.next_year) rows.push([`Medicare's ${d.next_year} amounts and rules, same schedule`, R[1] - R[0], null, 'policy: nobody\'s choice']);
  rows.push(['Your schedule', R[2] - R[1], null, 'the levers on the left']);
  rows.push([`Contracts corrected${nCorr ? ` (${nCorr})` : ''}`, R[3] - R[2], null, nCorr ? 'the ticks under Contracts' : 'none ticked yet']);
  v.querySelector('#calcFall').innerHTML = `
    <h2>Where the year lands</h2>
    <table class="calc-fall"><tbody>
      ${rows.map(([label, dv, val, note]) => `<tr class="${note==='muted'?'small muted':''}"><td>${esc(label)}${note && note!=='muted' ? `<div class="small muted">${esc(note)}</div>` : ''}</td>
        <td class="num">${val != null ? moneyK(val) : sign(dv) || '<span class="muted">no change</span>'}</td></tr>`).join('')}
      <tr class="calc-total"><td><b>Estimated collections, a year</b><div class="small muted">likely ${moneyK(lo)} to ${moneyK(hi)}: patient balances and the open ${esc(d.next_year || '')} readings</div></td>
        <td class="num"><b style="font-size:22px">${moneyK(R[3])}</b><div class="small muted">${money(fixed.totalCollections / Math.max(1, fixed.daysYear))} a clinic day · ${(fixed.daysYear||0).toFixed(0)} days</div></td></tr>
    </tbody></table>
    <div class="small muted" style="margin-top:6px">Of this, patient balances are about ${moneyK(fixed.patientBalances)} at the shares set under Patient balances.
      ${x.include_ancillary ? 'Ancillary and product revenue is included.' : `The practice's ancillary and product revenue (${moneyK(fixed.ancillaryCollections)} a year at this pace) is not included.`}</div>`;
  // where the money comes from: rows rounded so that they add up to the totals
  const lines = CalcModel.LINES.map(([k, label]) => [k, label, (nowNext.byLine[k] || {}).collections || 0, (fixed.byLine[k] || {}).collections || 0])
    .filter(r => (r[2] || r[3]) && (x.include_ancillary || !CalcModel.ANCILLARY.includes(r[0])));
  const extra = [];
  if(fixed.sameDay.collections || nowNext.sameDay.collections) extra.push([`Medicare's ${d.next_year} same-day rule`, -nowNext.sameDay.collections, -fixed.sameDay.collections]);
  if(x.include_ancillary && x.include_path) extra.push(['Pathology the biopsies bring the practice', nowNext.pathCollections, fixed.pathCollections]);
  const all = lines.map(([, label, a, b]) => [label, a, b]).concat(extra);
  const ra = calcRoundRows(all.map(r => r[1]), nowNext.totalCollections), rb = calcRoundRows(all.map(r => r[2]), fixed.totalCollections);
  const colA = `Now${x.year===d.next_year?', '+esc(d.next_year)+' rules':''}`, colB = `Plan${nCorr?' + contracts':''}`;
  v.querySelector('#calcLines').innerHTML = `<h2>Where it comes from, a year</h2>
    <table class="calc-lines"><thead><tr><th>Service line</th><th class="num">${colA}</th><th class="num">${colB}</th><th class="num">Change</th></tr></thead><tbody>
    ${all.map(([label], i) => `<tr><td>${esc(label)}</td><td class="num">${moneyK(ra[i])}</td><td class="num">${moneyK(rb[i])}</td><td class="num">${sign(rb[i] - ra[i])}</td></tr>`).join('')}
    <tr><td><b>Collections</b></td><td class="num"><b>${moneyK(R[1])}</b></td><td class="num"><b>${moneyK(R[3])}</b></td><td class="num">${sign(R[3] - R[1])}</td></tr>
    </tbody></table>`;
  calcPayers(v, calcWith(x, x, {year: x.year}));
  calcMoves(v);
  // the live notes beside sliders whose meaning depends on others
  const ak = v.querySelector('[data-extra="ak_patients"]'); if(ak) ak.textContent = ` · ${calcFmt.n1(CalcModel.akPatients(d, x).lesions)} lesions a day`;
  const bx = v.querySelector('[data-extra="bx_patients"]'); if(bx) bx.textContent = ` · ${calcFmt.n1(x.bx_patients * x.bx_per_patient)} biopsies a day`;
}

function calcPayers(v, plain){
  const cs = state.calc, d = cs.data, x = cs.x, Y = x.year, next = Y === d.next_year;
  const U = CalcModel.units(d, plain);
  const base = CalcModel.evaluate(d, plain, cs.cal);
  const list = Object.entries(d.payers).map(([g, p]) => {
    let pay = 0, med = 0, own = 0, all = 0;
    for(const [c, n] of Object.entries(U)){
      const s = (CalcModel.mixOf(d, c).find(e => e[0] === g) || [0, 0])[1];
      const M = (d.medicare[c] || {})[Y], r = CalcModel.price(d, plain, c, g, Y, false);
      if(s && M && r != null){ pay += n * s * r; med += n * s * M; }
      const row = (d.rates[c] || {})[g], rr = row && row[d.rate_year];
      if(s && rr){ all += n * s * rr; if(['own', 'medicare_schedule'].includes(row.how)) own += n * s * rr; }
    }
    const al = (base.byPayer[g] || {}).allowed || 0;
    let up = 0;
    if(p.correctable && al > 0){
      const one = Object.assign({}, plain, {corrected: {[g]: true}});
      up = CalcModel.evaluate(d, one, cs.cal).totalCollections - base.totalCollections;
    }
    return {g, kind: p.kind, correctable: p.correctable, negotiable: p.negotiable, pct: med ? pay / med : null, allowed: al, up, est: all > 0 && own / all < 0.5,
            indexed: CalcModel.isIndexed(d, x, g)};
  }).filter(r => r.allowed > 0).sort((a, b) => b.allowed - a.allowed);
  const total = list.reduce((t, r) => t + r.allowed, 0) || 1;
  const big = list.filter(r => r.allowed / total >= 0.005 || x.corrected[r.g]), small = list.filter(r => !big.includes(r));
  const tgt = g => x.targets[g] != null ? x.targets[g] : x.target_pct;
  const row = r => `<tr><td class="c-fix">${r.correctable ? `<input type="checkbox" data-calc-fix="${esc(r.g)}" ${x.corrected[r.g]?'checked':''} aria-label="correct ${esc(r.g)}">` : ''}</td>
      <td class="c-payer">${esc(r.g)}<div class="small muted">${esc(KIND_LABEL[r.kind] || r.kind)}${r.kind==='medicaid' ? (r.negotiable === 'rarely' ? ' · about the state\'s schedule, rarely negotiable' : ' · the state program sets these rates') : ''}${next && r.kind !== 'medicare' ? ` · <label><input type="checkbox" data-calc-idx="${esc(r.g)}" ${r.indexed?'checked':''}> moves with Medicare in ${esc(Y)}</label>` : ''}</div></td>
      <td class="num c-share">${r.allowed / total >= 0.005 ? Math.round(100 * r.allowed / total) + '%' : '<1%'}</td>
      <td class="num c-pays ${r.pct!=null && r.pct < x.target_pct - 0.005 ? 'sev-high' : ''}" data-label="Pays, vs Medicare">${r.pct==null?'–':Math.round(r.pct*100)+'%'}${r.est?' <span class="muted">est.</span>':''}</td>
      <td class="num c-target" ${r.correctable && x.corrected[r.g] ? 'data-label="Target"' : ''}>${r.correctable && x.corrected[r.g] ? `<input type="number" class="calc-num" data-calc-t="${esc(r.g)}" min="50" max="200" step="5" value="${Math.round(100 * tgt(r.g))}" aria-label="target for ${esc(r.g)}">%` : ''}</td>
      <td class="num c-up" ${r.correctable ? 'data-label="Corrected, a year"' : ''}>${r.correctable ? (Math.abs(r.up) >= 50000 ? (r.up > 0 ? '+' : '−') + moneyK(Math.abs(r.up)) : '–') : ''}</td></tr>`;
  const box = v.querySelector('#calcPayers');
  calcRedraw(box, `<h2>Contracts</h2>
    <div class="small muted">What each payer pays for this schedule as a share of Medicare's ${esc(Y)} amount for the same codes. Tick a contract to see it renegotiated.</div>
    <div class="filters" style="margin:6px 0;gap:8px;flex-wrap:wrap">
      <select id="calcMode" aria-label="how a contract is corrected">
        <option value="contract" ${x.fix_mode==='contract'?'selected':''}>Whole contract at one share of Medicare (some codes come down)</option>
        <option value="visits" ${x.fix_mode==='visits'?'selected':''}>Visit codes only at that share</option>
        <option value="floor" ${x.fix_mode==='floor'?'selected':''}>Every code raised to at least that share (upper bound)</option>
      </select>
      <label class="small">at <input type="number" id="calcTarget" min="50" max="200" step="5" value="${Math.round(x.target_pct*100)}" style="width:64px"> % of Medicare</label>
      <button id="calcAllBelow">Tick every commercial and Medicare Advantage contract below it</button> <button id="calcNone" class="ghost">Clear</button>
    </div>
    <table class="calc-payers"><thead><tr><th></th><th>Payer</th><th class="num">Share</th><th class="num">Pays, vs Medicare</th><th class="num">Target</th><th class="num">Corrected, a year</th></tr></thead><tbody>
    ${big.map(row).join('')}
    </tbody></table>
    ${small.length ? `<details style="margin-top:6px" data-fold="payers-small" ${calcFold('payers-small', false)}><summary class="small">${small.length} payers with under half a percent of the money each</summary>
      <table class="calc-payers"><tbody>${small.map(row).join('')}</tbody></table></details>` : ''}
    <div class="small muted" style="margin-top:6px">est. = most of this payer's rates for these codes are not in the remittances and are estimated from its own percentage of Medicare. Medicaid managed care contracts mostly follow the state's schedule and are rarely negotiable, so they are not counted as correctable.</div>`);
  // changes redraw on the next frame, after the focus has moved on (Tab), so the focus stays where the reader put it
  box.querySelectorAll('[data-calc-fix]').forEach(cb => cb.onchange = () => { x.corrected[cb.dataset.calcFix] = cb.checked; calcSoon(); });
  box.querySelectorAll('[data-calc-idx]').forEach(cb => cb.onchange = () => { x.indexed[cb.dataset.calcIdx] = cb.checked; calcSoon(); });
  // a share of Medicare from 50% to 200%; anything else puts the last good figure back in the box
  const pctBox = (inp, get, set) => inp.onchange = () => { const n = parseFloat(inp.value); if(isFinite(n) && inp.value !== '') set(Math.min(2, Math.max(0.5, n / 100))); inp.value = Math.round(get() * 100); calcSoon(); };
  box.querySelectorAll('[data-calc-t]').forEach(inp => { const g = inp.dataset.calcT; pctBox(inp, () => tgt(g), t => { x.targets[g] = t; }); });
  pctBox(box.querySelector('#calcTarget'), () => x.target_pct, t => { x.target_pct = t; });
  box.querySelector('#calcMode').onchange = e => { x.fix_mode = e.target.value; calcSoon(); };
  box.querySelector('#calcAllBelow').onclick = () => { for(const r of list) if(r.correctable && r.pct != null && r.pct < x.target_pct - 0.005) x.corrected[r.g] = true; calcSoon(); };
  box.querySelector('#calcNone').onclick = () => { x.corrected = {}; calcSoon(); };
}

function calcMoves(v){
  const cs = state.calc, d = cs.data, x = cs.x;
  const moves = CalcModel.marginal(d, x, cs.cal);
  const val = m => Math.abs(m.value) < 50000 ? '<span class="muted">–</span>' : `${m.value > 0 ? '+' : '−'}${moneyK(Math.abs(m.value))}`;
  const tbl = list => `<table class="calc-goal"><tbody>${list.map(m => `<tr><td>${esc(m.label)}</td><td class="num">${val(m)}</td></tr>`).join('')}</tbody></table>`;
  const clinical = moves.filter(m => m.kind === 'clinical');
  calcRedraw(v.querySelector('#calcMoves'), `<h2>What one more is worth</h2>
    <div class="small muted">Collections a year, with the rest of the plan as it stands and the contracts as ticked.</div>
    ${tbl(moves.filter(m => m.kind === 'volume'))}
    ${clinical.length ? `<details style="margin-top:6px" data-fold="moves-mix" ${calcFold('moves-mix', false)}><summary class="small">And what the case mix moves (clinical outcomes, not targets)</summary>${tbl(clinical)}</details>` : ''}`);
  const box = v.querySelector('#calcGoal');
  if(!box.dataset.ready){
    box.dataset.ready = '1';
    box.innerHTML = `<h2>What a target takes</h2>
      <div class="filters"><label class="small" for="calcTargetY">Collections a year</label> <input id="calcTargetY" type="text" autocomplete="off" placeholder="1,600,000 or 1.6m" value="${esc(cs.target)}" style="width:150px"></div>
      <div id="calcGoalOut" class="small" aria-live="polite"></div>`;
    box.querySelector('#calcTargetY').oninput = e => { cs.target = e.target.value; calcGoal(); };
  }
  calcGoal();
}

function calcGoal(){
  const cs = state.calc, d = cs.data, x = cs.x, out = cs.el.querySelector('#calcGoalOut');
  if(!out) return;
  const raw = String(cs.target || '').trim();
  if(!raw){ out.innerHTML = '<span class="muted">Type a yearly figure to see what a clinic day has to bring in, and how much more of each thing it takes with everything else held where the plan has it.</span>'; return; }
  const dollars = calcMoney(raw);
  if(!isFinite(dollars) || dollars <= 0){ out.innerHTML = '<span class="sev-medium">That is not a yearly dollar figure. Type one above zero, such as 1,600,000, 1.6m or 950k.</span>'; return; }
  if(dollars > 1e9){ out.innerHTML = '<span class="sev-medium">That is more than a billion dollars a year. Type one clinician\'s yearly figure, such as 1,600,000 or 1.6m.</span>'; return; }
  const T = dollars * 100;
  const plan = CalcModel.evaluate(d, x, cs.cal);
  const res = CalcModel.goalSeek(d, x, T, cs.cal);
  const days = Math.max(1, plan.daysYear), perDay = T / days;
  const ratio = plan.allowed > 0 ? plan.totalCollections / plan.allowed : 1;
  const dist = d.schedule.clinic_day_allowed || {};
  const needAllowed = perDay / Math.max(0.01, ratio);
  const where = dist.p50 ? (needAllowed <= dist.p50 ? 'at or below your median clinic day' : needAllowed <= (dist.p75 || 0) ? 'between your median and your busier days' :
    needAllowed <= (dist.p90 || 0) ? 'near your busiest tenth of days' : 'above your busiest tenth of days: every day would have to beat your best days so far') : '';
  const head = `<p>${T < 10000000 ? money(T) : moneyK(T)} over ${days.toFixed(0)} clinic days is ${money(perDay)} collected a clinic day (about ${money(needAllowed)} allowed).
    ${dist.p50 ? `Your clinic days have brought ${money(dist.p50)} allowed at the median and ${money(dist.p90)} at the 90th percentile: this is ${where}.` : ''}</p>`;
  // the gap is the target less the plan, whichever levers are worth something for this clinician
  const gap = res.gap != null ? res.gap : T - plan.totalCollections;
  if(gap <= 0){ out.innerHTML = head + `<p class="ok">The plan already reaches it, with ${moneyK(-gap)} to spare.</p>`; return; }
  const ways = res.filter(m => m.needed != null && m.needed > 0);
  if(!ways.length){ out.innerHTML = head + `<p>${moneyK(gap)} a year short. None of the schedule's levers adds collections for this clinician on its own.</p>`; return; }
  const dpw = Math.max(0.5, x.days_per_week);
  const say = m => {
    const n = m.needed;
    if(m.key === 'days_per_week') return x.days_per_week + n * 0.5 > 6 ? 'not enough on its own (more than 6 days a week)' : `${(n * 0.5).toFixed(1)} more clinic days a week`;
    if(m.key === 'weeks_per_year') return x.weeks_per_year + n > 52 ? 'not enough on its own (more than 52 weeks)' : `${n.toFixed(1)} more weeks a year`;
    return `${n.toFixed(1)} more a week (${(n / dpw).toFixed(1)} a clinic day)`;
  };
  out.innerHTML = head + `<p>${moneyK(gap)} a year short. Any one of these closes it on its own:</p>
    <table class="calc-goal"><tbody>${ways.map(m => `<tr><td>${esc(m.name)}</td><td class="num">${esc(say(m))}</td></tr>`).join('')}</tbody></table>
    <div class="muted" style="margin-top:4px">Stages, closures, visit levels and lesions per patient are left out on purpose: they follow the patient.</div>`;
}

function calcHow(d){
  const src = d.rate_sources || {}, n = Object.values(src).reduce((a, b) => a + b, 0) || 1;
  const pct = k => Math.round(100 * (src[k] || 0) / n);
  return `<p>The schedule is turned into billed codes with this clinician's own mix: which Mohs codes, which flaps and grafts, which excision sizes, which visit levels. Each code is priced at every payer's own usual allowed amount in the payer mix of that service line, as the remittances show it.</p>
  <p>The months read are the last six complete ones by default: the run-rate now. A longer window reaches back past changes that are not coming back, such as January 2026, when skin-substitute payment collapsed. Days a week and weeks a year come from the last twelve months, so holidays count.</p>
  <p>Where a payer's rate for a code is not in the remittances, the code is priced at that payer's own percentage of Medicare for the same kind of service times Medicare's published amount (${pct('payer_pct_of_medicare')}% of payer-and-code pairs, mostly rare codes); Medicare itself is priced from CMS's fee schedule for Kentucky and Indiana, weighted by where the clinic days were.</p>
  <p>When a Mohs first stage and a flap, graft or layered closure are billed together, Medicare's multiple-procedure rule pays the lower of the two at half: with a flap or graft that is the Mohs first stage, with a layered closure it is the closure. The same for an excision and its layered closure. That pairing is worked out case by case, so a change in how cases are closed moves it. Extra stages and add-on codes are paid in full.</p>
  <p>What the usual rates do not see (denials that stayed denied, later procedures of a day, units, underpayments) is carried code by code by comparing what payers actually allowed in the window with what this model says the same schedule was worth then. Where the figures split a code by kind of payer, each kind with enough lines has its own comparison, so one plan's denials stay with that plan.</p>
  <p>Collections are what the payers paid (second payers included, from the remittances) plus the share of patient balances you set as collected, by kind of payer; nothing is counted from Medicaid patients' balances. The payers' share is this clinician's own for each kind of payer, moved by how much more patients owe on a visit (copayments) than on a procedure.</p>
  <p>Contracts: "whole contract" sets every code at one share of Medicare, as a renegotiated schedule would, so codes now paid above it come down; "visit codes only" is the common carve-out ask; "every code raised" is an upper bound no payer signs.</p>
  ${d.next_year ? `<p>${esc(d.next_year)}: CMS's ${esc(d.next_year)} relative values, conversion factor and geographic indices for Kentucky and Indiana. The ${esc(d.rate_year)} amounts include a 1.0 floor on the work index that the ${esc(d.next_year)} file does not, so these assume Congress lets it lapse. Payers ticked as moving with Medicare follow its amounts; the rest keep their ${esc(d.rate_year)} rates. G2211 is replaced by modifier B1 (16% of the visit). The same-day rule (CMS-1848-F, 91 FR 71121) takes 20% off every service except the highest when an office visit billed with modifier 25 shares the day with a 0-day procedure. Only biopsy days and Mohs days are modelled here; other 0-day services (shaves, intralesional injections, simple repairs, PDT) are not, so the loss is a little larger for schedules with many of them. A visit with only frozen lesions, excisions or layered repairs (10-day codes) does not bring it in. It binds traditional Medicare; Medicare Advantage plans apply it only by contract, and one switch covers both B1 and this rule for the plans that move with Medicare. A visit with modifier 57 is not counted unless the sensitivity for it is ticked. How it meets the 50% multiple-procedure cut is not settled: the page applies the 20% to what is left after it.</p>` : ''}
  <p>Not modelled: services billed under another clinician's number, no-shows, self-pay and cosmetic work (not on remittances), and where a payer's rate depends on the plan rather than the payer.</p>`;
}
