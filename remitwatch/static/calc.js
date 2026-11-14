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

async function calculatorTab(v){
  const cs = state.calc = Object.assign({provider: 'mohs', months: 6, data: null, x: null, cal: null, target: ''}, state.calc || {});
  state.pv = state.pv || {};
  cs.el = v;
  if(pvLocked() && !state.ownerKey){ pvUnlockForm(v); return; }
  const key = cs.provider + '|' + cs.months;
  if(!cs.data || cs.data._for !== key){
    v.classList.add('busy');
    try {
      const d = await api('/api/activity/calculator?' + qs({provider: cs.provider, months: cs.months}));
      if(state.tab !== 'calculator') return;
      if(!d.ready){ v.classList.remove('busy'); v.innerHTML = `<div class="panel"><h2>Collections calculator</h2><div class="note">${esc(d.message || 'The figures are not built yet.')}</div></div>`; return; }
      d._for = key;
      const keep = cs.x && cs.data && cs.data.who && cs.data.who.id === d.who.id ? {year: cs.x.year} : {};
      cs.data = d; cs.cal = CalcModel.calibrate(d); cs.base = CalcModel.defaults(d); cs.x = Object.assign(JSON.parse(JSON.stringify(cs.base)), keep);
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
  v.innerHTML = `
  <div class="panel">
    <h2 style="margin-bottom:4px">Collections calculator</h2>
    <div class="small muted">${esc(calcWho(d))}. Built from services in ${esc(d.from)} to ${esc(d.to)} (${(d.schedule.clinic_days||0).toLocaleString()} clinic days of 5 or more patients), each payer at its own rates.</div>
    <div class="filters" style="margin-top:8px;gap:8px;flex-wrap:wrap">
      <div class="segs" role="group" aria-label="whose year">
        ${roles.map(([r]) => `<button class="seg ${cs.provider===r?'on':''}" data-calc-who="${r}">${esc(ROLE_LABEL[r])} (average)</button>`).join('')}
      </div>
      <select id="calcWho" aria-label="clinician">
        <option value="">One clinician…</option>
        <optgroup label="Physicians">${people.filter(c=>c.tier==='physician').map(c=>opt(c.provider_id, c.name)).join('')}</optgroup>
        <optgroup label="APPs">${people.filter(c=>c.tier==='nonphysician').map(c=>opt(c.provider_id, c.name)).join('')}</optgroup>
      </select>
      <select id="calcMonths" aria-label="months read">
        <option value="6" ${cs.months==6?'selected':''}>Last 6 months (run-rate now)</option>
        <option value="12" ${cs.months==12?'selected':''}>Last 12 months</option>
      </select>
      ${y27 ? `<div class="segs" role="group" aria-label="year planned">
        <button class="seg ${x.year===y27?'on':''}" data-calc-year="${esc(y27)}">Plan ${esc(y27)}</button>
        <button class="seg ${x.year===d.rate_year?'on':''}" data-calc-year="${esc(d.rate_year)}">${esc(d.rate_year)} rates</button></div>` : ''}
      <button id="calcReset" class="ghost">Back to the schedule now</button>
    </div>
  </div>
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
      <details class="panel" id="calcHow"><summary><b>How this is worked out</b></summary><div id="calcHowBody" class="small"></div></details>
    </div>
  </div>`;
  v.querySelectorAll('[data-calc-who]').forEach(b => b.onclick = () => { cs.provider = b.dataset.calcWho; render(); });
  v.querySelector('#calcWho').onchange = e => { if(e.target.value){ cs.provider = e.target.value; render(); } };
  v.querySelector('#calcMonths').onchange = e => { cs.months = +e.target.value; render(); };
  v.querySelectorAll('[data-calc-year]').forEach(b => b.onclick = () => { x.year = b.dataset.calcYear; calcDraw(v); });
  v.querySelector('#calcReset').onclick = () => {
    const keep = {year: x.year, corrected: x.corrected, targets: x.targets, target_pct: x.target_pct, fix_mode: x.fix_mode, indexed: x.indexed, q: x.q};
    cs.x = Object.assign(JSON.parse(JSON.stringify(cs.base)), keep); calcDraw(v); };
  calcInputs(v);
  calcUpdate();
  v.querySelector('#calcHowBody').innerHTML = calcHow(d);
}

function calcRow(cs, def, otherNow){
  const [k, label, mn, mx, st, f, grp] = def, x = cs.x;
  if(grp && !(otherNow[grp] > 0)) return '';
  const now = cs.base[k], val = x[k], pctLike = f === 'pct' || f === 'per100';
  const hi = Math.max(mx, Math.ceil((now || 0) * 1.5 / st) * st);
  const extra = k === 'ak_patients' ? ` · ${calcFmt.n1(CalcModel.akPatients(cs.data, x).lesions)} lesions a day`
    : k === 'bx_patients' ? ` · ${calcFmt.n1(x.bx_patients * x.bx_per_patient)} biopsies a day`
    : k === 'benign' ? ' · 17110 pays once per patient for 1 to 14 lesions'
    : grp ? ` of ${money(otherNow[grp])} allowed a clinic day` : '';
  return `<div class="calc-row" data-k="${k}">
    <label for="cr_${k}">${esc(label)}</label>
    <div class="calc-ctl"><input type="range" id="cr_${k}" min="${mn}" max="${hi}" step="${st}" value="${val}">
      <input type="number" class="calc-num" aria-label="${esc(label)}" min="${mn}" step="${pctLike?1:st}" value="${pctLike ? Math.round(val*100) : (+val).toFixed(f==='n2'?2:f==='n1'?1:0)}">${f==='pct'?'<span class="small">%</span>':''}</div>
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
  const box = v.querySelector('#calcInputs');
  box.innerHTML = `<h2>The schedule</h2>
    <div class="small muted" style="margin-bottom:6px">"Now" is ${esc(ROLE_LABEL[d.who.id] ? 'the average' : 'this clinician')}'s own figure over the months read. These are the levers a schedule controls; goal seeking moves only these.</div>
    ${block(CALC_VOLUME)}
    <details style="margin-top:10px"><summary><b>How cases go</b> <span class="small muted">clinical outcomes, not targets</span></summary>
      <div class="small muted" style="margin:4px 0">Stages, closures, visit levels and lesions per patient follow what each patient needs and what the note supports, and payers audit them. Use these to see what a change in case mix would do, not as goals.</div>
      ${block(CALC_CLINICAL)}</details>
    <details style="margin-top:6px"><summary><b>Patient balances</b> <span class="small muted">share collected, by kind of payer</span></summary>
      <div class="small muted" style="margin:4px 0">The remittances show what a patient was left to owe, not what the patient paid. These shares are assumptions until the practice's patient-payment figures are loaded. Nothing is counted from Medicaid patients.</div>
      ${CALC_Q.map(([k, label]) => `<div class="calc-row" data-q="${k}"><label for="cq_${k}">${esc(label)}</label>
        <div class="calc-ctl"><input type="range" id="cq_${k}" min="0" max="1" step="0.01" value="${x.q[k]}"><input type="number" class="calc-num" min="0" max="100" step="1" value="${Math.round(x.q[k]*100)}"><span class="small">%</span></div></div>`).join('')}
    </details>
    ${next ? `<details style="margin-top:6px" open><summary><b>Medicare's ${esc(d.next_year)} same-day rule</b></summary>
      ${CALC_2027.map(r => calcRow(cs, r, otherNow)).join('')}
      <label class="small" style="display:block"><input type="checkbox" id="calcMASD" ${x.ma_same_day?'checked':''}> Medicare Advantage plans apply it too (the rule binds traditional Medicare; a plan applies it only if its contract follows Medicare's payment rules)</label>
      <label class="small" style="display:block"><input type="checkbox" id="calcAdd" ${x.cut_add_ons?'checked':''}> It cuts add-on codes too (extra Mohs stages, extra biopsies, 17003). The rule says every service but the highest; CMS has not said how add-ons are handled.</label>
    </details>` : ''}
    <details style="margin-top:6px"><summary><b>The practice's ancillary and product revenue</b> <span class="small muted">not this clinician's production</span></summary>
      <div class="small muted" style="margin:4px 0">Radiation, skin-substitute products, pathology, drugs and staff services. They come to the practice, carry costs of their own, and under the Stark rules cannot be credited to a clinician as production. They are left out of the figures above unless you add them here.</div>
      <label class="small" style="display:block"><input type="checkbox" id="calcAnc" ${x.include_ancillary?'checked':''}> Add them to the totals</label>
      ${ancRows}
      <label class="small" style="display:block;margin-top:6px"><input type="checkbox" id="calcPath" ${x.include_path?'checked':''}> Also the pathology this clinician's biopsies bring the practice (read and billed by the practice's own reader)</label>
    </details>`;
  const all = CALC_VOLUME.concat(CALC_CLINICAL).flatMap(g => g[1]).concat(CALC_2027).concat(CALC_ANCILLARY.map(([k, l, g]) => [k, l, 0, 3, 0.05, 'pct', g]));
  box.querySelectorAll('.calc-row[data-k]').forEach(r => {
    const k = r.dataset.k, def = all.find(s => s[0] === k), pct = def[5] === 'pct' || def[5] === 'per100';
    const range = r.querySelector('input[type=range]'), num = r.querySelector('.calc-num');
    range.oninput = () => { x[k] = +range.value; num.value = pct ? Math.round(x[k] * 100) : range.value; calcSoon(); };
    num.oninput = () => { const n = parseFloat(num.value); if(!isFinite(n)) return; x[k] = pct ? n / 100 : n; range.value = x[k]; calcSoon(); };
  });
  box.querySelectorAll('.calc-row[data-q]').forEach(r => {
    const k = r.dataset.q, range = r.querySelector('input[type=range]'), num = r.querySelector('.calc-num');
    range.oninput = () => { x.q[k] = +range.value; num.value = Math.round(x.q[k] * 100); calcSoon(); };
    num.oninput = () => { const n = parseFloat(num.value); if(!isFinite(n)) return; x.q[k] = Math.min(1, Math.max(0, n / 100)); range.value = x.q[k]; calcSoon(); };
  });
  const on = (id, f) => { const el = box.querySelector(id); if(el) el.onchange = e => { f(e.target.checked); calcUpdate(); }; };
  on('#calcMASD', c => x.ma_same_day = c); on('#calcAdd', c => x.cut_add_ons = c);
  on('#calcAnc', c => x.include_ancillary = c); on('#calcPath', c => x.include_path = c);
}

let calcTimer = null;
function calcSoon(){ if(calcTimer) return; calcTimer = requestAnimationFrame(() => { calcTimer = null; calcUpdate(); }); }

// the same assumptions (collections, 2027 readings, contracts, ancillary) on another schedule or year
function calcWith(base, x, over){
  return Object.assign({}, base, {q: x.q, ma_same_day: x.ma_same_day, cut_add_ons: x.cut_add_ons, bx_visit_share: x.bx_visit_share,
    mohs_visit_share: x.mohs_visit_share, include_ancillary: x.include_ancillary, include_path: x.include_path, indexed: x.indexed,
    fix_mode: x.fix_mode, target_pct: x.target_pct, targets: x.targets, corrected: {}}, over || {});
}

function calcUpdate(){
  const cs = state.calc; if(!cs || !cs.data || !cs.el || !cs.el.isConnected) return;
  const d = cs.data, x = cs.x, v = cs.el, cal = cs.cal;
  const ev = y => CalcModel.evaluate(d, y, cal);
  const nowRate = ev(calcWith(cs.base, x, {year: d.rate_year}));
  const nowNext = x.year === d.next_year ? ev(calcWith(cs.base, x, {year: d.next_year})) : nowRate;
  const plan = ev(calcWith(x, x, {year: x.year}));
  const fixed = ev(Object.assign({}, x));
  const nCorr = Object.values(x.corrected).filter(Boolean).length;
  const sign = dv => Math.abs(dv) < 500 ? '' : `<span class="${dv>0?'ok':'sev-high'}">${dv>0?'+':'−'}${moneyK(Math.abs(dv))}</span>`;
  // the band: patient balances and the open 2027 readings
  const qShift = s => Object.fromEntries(Object.entries(x.q).map(([k, q]) => [k, Math.min(1, Math.max(0, q + s))]));
  const lo = ev(Object.assign({}, x, {q: qShift(-0.15), cut_add_ons: true, ma_same_day: true})).totalCollections;
  const hi = ev(Object.assign({}, x, {q: qShift(0.10), cut_add_ons: false, ma_same_day: false})).totalCollections;
  // what payers allowed in the window for this clinician's own production, at a year's pace
  const anc = new Set(CalcModel.ANCILLARY);
  let prodActual = 0;
  for(const [c, cc] of Object.entries(d.codes)){ const g = (d.levers.other.groups || {})[c]; if(!(g && anc.has(g))) prodActual += cc.allowed_cents || 0; }
  const perYear = cs.base.days_per_week * cs.base.weeks_per_year / Math.max(1, d.schedule.clinic_days);
  const rows = [
    ['Allowed by payers for this production, read at a year\'s pace', null, prodActual * perYear, 'muted'],
    [`Collections now: the schedule as it is, ${d.rate_year} rates`, null, nowRate.totalCollections, ''],
  ];
  if(x.year === d.next_year) rows.push([`Medicare's ${d.next_year} amounts and rules, same schedule`, nowNext.totalCollections - nowRate.totalCollections, null, 'policy: nobody\'s choice']);
  rows.push(['Your schedule', plan.totalCollections - nowNext.totalCollections, null, 'the levers on the left']);
  rows.push([`Contracts corrected${nCorr ? ` (${nCorr})` : ''}`, fixed.totalCollections - plan.totalCollections, null, nCorr ? 'the ticks under Contracts' : 'none ticked yet']);
  v.querySelector('#calcFall').innerHTML = `
    <h2>Where the year lands</h2>
    <table class="calc-fall"><tbody>
      ${rows.map(([label, dv, val, note]) => `<tr class="${note==='muted'?'small muted':''}"><td>${esc(label)}${note && note!=='muted' ? `<div class="small muted">${esc(note)}</div>` : ''}</td>
        <td class="num">${val != null ? moneyK(val) : sign(dv) || '<span class="muted">no change</span>'}</td></tr>`).join('')}
      <tr class="calc-total"><td><b>Estimated collections, a year</b><div class="small muted">likely ${moneyK(lo)} to ${moneyK(hi)}: patient balances and the open 2027 readings</div></td>
        <td class="num"><b style="font-size:22px">${moneyK(fixed.totalCollections)}</b><div class="small muted">${money(fixed.totalCollections / Math.max(1, fixed.daysYear))} a clinic day · ${(fixed.daysYear||0).toFixed(0)} days</div></td></tr>
    </tbody></table>
    <div class="small muted" style="margin-top:6px">Of this, patient balances are about ${moneyK(fixed.patientBalances)} at the shares set under Patient balances.
      ${x.include_ancillary ? 'Ancillary and product revenue is included.' : `The practice's ancillary and product revenue (${moneyK(fixed.ancillaryCollections)} a year at this pace) is not included.`}</div>`;
  // where the money comes from
  const lines = CalcModel.LINES.map(([k, label]) => [k, label, (nowNext.byLine[k] || {}).collections || 0, (fixed.byLine[k] || {}).collections || 0])
    .filter(r => (r[2] || r[3]) && (x.include_ancillary || !CalcModel.ANCILLARY.includes(r[0])));
  const extra = [];
  if(fixed.sameDay.collections || nowNext.sameDay.collections) extra.push([`Medicare's ${d.next_year} same-day rule`, -nowNext.sameDay.collections, -fixed.sameDay.collections]);
  if(x.include_ancillary && x.include_path) extra.push(['Pathology the biopsies bring the practice', nowNext.pathCollections, fixed.pathCollections]);
  v.querySelector('#calcLines').innerHTML = `<h2>Where it comes from, a year</h2>
    <table><thead><tr><th>Service line</th><th class="num">Now${x.year===d.next_year?', '+esc(d.next_year)+' rules':''}</th><th class="num">Plan${nCorr?' + contracts':''}</th><th class="num">Change</th></tr></thead><tbody>
    ${lines.map(([, label, a, b]) => [label, a, b]).concat(extra).map(([label, a, b]) => `<tr><td>${esc(label)}</td><td class="num">${moneyK(a)}</td><td class="num">${moneyK(b)}</td><td class="num">${sign(b - a)}</td></tr>`).join('')}
    <tr><td><b>Collections</b></td><td class="num"><b>${moneyK(nowNext.totalCollections)}</b></td><td class="num"><b>${moneyK(fixed.totalCollections)}</b></td><td class="num">${sign(fixed.totalCollections - nowNext.totalCollections)}</td></tr>
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
    return {g, kind: p.kind, correctable: p.correctable, pct: med ? pay / med : null, allowed: al, up, est: all > 0 && own / all < 0.5,
            indexed: CalcModel.isIndexed(d, x, g)};
  }).filter(r => r.allowed > 0).sort((a, b) => b.allowed - a.allowed);
  const total = list.reduce((t, r) => t + r.allowed, 0) || 1;
  const big = list.filter(r => r.allowed / total >= 0.005 || x.corrected[r.g]), small = list.filter(r => !big.includes(r));
  const row = r => `<tr><td>${r.correctable ? `<input type="checkbox" data-calc-fix="${esc(r.g)}" ${x.corrected[r.g]?'checked':''} aria-label="correct ${esc(r.g)}">` : ''}</td>
      <td>${esc(r.g)}<div class="small muted">${esc(KIND_LABEL[r.kind] || r.kind)}${r.kind==='medicaid'?' · the state program sets these rates':''}${next && r.kind !== 'medicare' ? ` · <label><input type="checkbox" data-calc-idx="${esc(r.g)}" ${r.indexed?'checked':''}> moves with Medicare in ${esc(Y)}</label>` : ''}</div></td>
      <td class="num">${r.allowed / total >= 0.005 ? Math.round(100 * r.allowed / total) + '%' : '<1%'}</td>
      <td class="num ${r.pct!=null && r.pct < x.target_pct - 0.005 ? 'sev-high' : ''}">${r.pct==null?'–':Math.round(r.pct*100)+'%'}${r.est?' <span class="muted">est.</span>':''}</td>
      <td class="num">${r.correctable && x.corrected[r.g] ? `<input type="number" class="calc-num" data-calc-t="${esc(r.g)}" min="50" max="200" step="5" value="${Math.round(100*(x.targets[r.g] != null ? x.targets[r.g] : x.target_pct))}" aria-label="target for ${esc(r.g)}">%` : ''}</td>
      <td class="num">${r.correctable ? (Math.abs(r.up) > 500 ? (r.up > 0 ? '+' : '−') + moneyK(Math.abs(r.up)) : '–') : ''}</td></tr>`;
  const box = v.querySelector('#calcPayers');
  box.innerHTML = `<h2>Contracts</h2>
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
    <table><thead><tr><th></th><th>Payer</th><th class="num">Share</th><th class="num">Pays, vs Medicare</th><th class="num">Target</th><th class="num">Corrected, a year</th></tr></thead><tbody>
    ${big.map(row).join('')}
    </tbody></table>
    ${small.length ? `<details style="margin-top:6px"><summary class="small">${small.length} payers with under half a percent of the money each</summary>
      <table><tbody>${small.map(row).join('')}</tbody></table></details>` : ''}
    <div class="small muted" style="margin-top:6px">est. = most of this payer's rates for these codes are not in the remittances and are estimated from its own percentage of Medicare. Medicaid plans follow the state's schedule and are not counted as correctable.</div>`;
  box.querySelectorAll('[data-calc-fix]').forEach(cb => cb.onchange = () => { x.corrected[cb.dataset.calcFix] = cb.checked; calcUpdate(); });
  box.querySelectorAll('[data-calc-idx]').forEach(cb => cb.onchange = () => { x.indexed[cb.dataset.calcIdx] = cb.checked; calcUpdate(); });
  box.querySelectorAll('[data-calc-t]').forEach(inp => inp.onchange = () => { const n = parseFloat(inp.value); if(isFinite(n) && n > 0){ x.targets[inp.dataset.calcT] = n / 100; calcUpdate(); } });
  box.querySelector('#calcMode').onchange = e => { x.fix_mode = e.target.value; calcUpdate(); };
  box.querySelector('#calcTarget').onchange = e => { const n = parseFloat(e.target.value); if(isFinite(n) && n > 0){ x.target_pct = n / 100; calcUpdate(); } };
  box.querySelector('#calcAllBelow').onclick = () => { for(const r of list) if(r.correctable && r.pct != null && r.pct < x.target_pct - 0.005) x.corrected[r.g] = true; calcUpdate(); };
  box.querySelector('#calcNone').onclick = () => { x.corrected = {}; calcUpdate(); };
}

function calcMoves(v){
  const cs = state.calc, d = cs.data, x = cs.x;
  const moves = CalcModel.marginal(d, x, cs.cal);
  const tbl = list => `<table><tbody>${list.map(m => `<tr><td>${esc(m.label)}</td><td class="num">${m.value >= 0 ? '+' : '−'}${moneyK(Math.abs(m.value))}</td></tr>`).join('')}</tbody></table>`;
  v.querySelector('#calcMoves').innerHTML = `<h2>What one more is worth</h2>
    <div class="small muted">Collections a year, with the rest of the plan as it stands and the contracts as ticked.</div>
    ${tbl(moves.filter(m => m.kind === 'volume'))}
    <details style="margin-top:6px"><summary class="small">And what the case mix moves (clinical outcomes, not targets)</summary>${tbl(moves.filter(m => m.kind === 'clinical'))}</details>`;
  const box = v.querySelector('#calcGoal');
  if(!box.dataset.ready){
    box.dataset.ready = '1';
    box.innerHTML = `<h2>What a target takes</h2>
      <div class="filters"><label class="small" for="calcTargetY">Collections a year</label> <input id="calcTargetY" type="text" inputmode="numeric" placeholder="1,600,000" value="${esc(cs.target)}" style="width:140px"></div>
      <div id="calcGoalOut" class="small"></div>`;
    box.querySelector('#calcTargetY').oninput = e => { cs.target = e.target.value; calcGoal(); };
  }
  calcGoal();
}

function calcGoal(){
  const cs = state.calc, d = cs.data, x = cs.x, out = cs.el.querySelector('#calcGoalOut');
  if(!out) return;
  const t = parseFloat(String(cs.target || '').replace(/[$,\s]/g, '').replace(/k$/i, '000').replace(/m$/i, '000000'));
  if(!isFinite(t) || t <= 0){ out.innerHTML = '<span class="muted">Type a yearly figure to see what a clinic day has to bring in, and how much more of each thing it takes with everything else held where the plan has it.</span>'; return; }
  const plan = CalcModel.evaluate(d, x, cs.cal);
  const res = CalcModel.goalSeek(d, x, t * 100, cs.cal);
  const days = Math.max(1, plan.daysYear), perDay = t * 100 / days;
  const ratio = plan.allowed > 0 ? plan.totalCollections / plan.allowed : 1;
  const dist = d.schedule.clinic_day_allowed || {};
  const needAllowed = perDay / Math.max(0.01, ratio);
  const where = dist.p50 ? (needAllowed <= dist.p50 ? 'at or below your median clinic day' : needAllowed <= (dist.p75 || 0) ? 'between your median and your busier days' :
    needAllowed <= (dist.p90 || 0) ? 'near your busiest tenth of days' : 'above your busiest tenth of days: every day would have to beat your best days so far') : '';
  const head = `<p>${moneyK(t * 100)} over ${days.toFixed(0)} clinic days is ${money(perDay)} collected a clinic day (about ${money(needAllowed)} allowed).
    ${dist.p50 ? `Your clinic days have brought ${money(dist.p50)} allowed at the median and ${money(dist.p90)} at the 90th percentile: this is ${where}.` : ''}</p>`;
  const gap = res.length ? res[0].gap : 0;
  if(gap <= 0){ out.innerHTML = head + `<p class="ok">The plan already reaches it, with ${moneyK(-gap)} to spare.</p>`; return; }
  const dpw = Math.max(0.5, x.days_per_week);
  const say = m => {
    const n = m.needed;
    if(m.key === 'days_per_week') return x.days_per_week + n * 0.5 > 6 ? 'not enough on its own (more than 6 days a week)' : `${(n * 0.5).toFixed(1)} more clinic days a week`;
    if(m.key === 'weeks_per_year') return x.weeks_per_year + n > 52 ? 'not enough on its own (more than 52 weeks)' : `${n.toFixed(1)} more weeks a year`;
    return `${n.toFixed(1)} more a week (${(n / dpw).toFixed(1)} a clinic day)`;
  };
  out.innerHTML = head + `<p>${moneyK(gap)} a year short. Any one of these closes it on its own:</p>
    <table><tbody>${res.filter(m => m.needed != null).map(m => `<tr><td>${esc(m.name)}</td><td class="num">${esc(say(m))}</td></tr>`).join('')}</tbody></table>
    <div class="muted" style="margin-top:4px">Stages, closures, visit levels and lesions per patient are left out on purpose: they follow the patient.</div>`;
}

function calcHow(d){
  const src = d.rate_sources || {}, n = Object.values(src).reduce((a, b) => a + b, 0) || 1;
  const pct = k => Math.round(100 * (src[k] || 0) / n);
  return `<p>The schedule is turned into billed codes with this clinician's own mix: which Mohs codes, which flaps and grafts, which excision sizes, which visit levels. Each code is priced at every payer's own usual allowed amount in the payer mix of that service line, as the remittances show it.</p>
  <p>The months read are the last six complete ones by default: the run-rate now. A longer window reaches back past changes that are not coming back, such as January 2026, when skin-substitute payment collapsed. Days a week and weeks a year come from the last twelve months, so holidays count.</p>
  <p>Where a payer's rate for a code is not in the remittances, the code is priced at that payer's own percentage of Medicare for the same kind of service times Medicare's published amount (${pct('payer_pct_of_medicare')}% of payer-and-code pairs, mostly rare codes); Medicare itself is priced from CMS's fee schedule for Kentucky and Indiana, weighted by where the clinic days were.</p>
  <p>When a Mohs first stage and a flap, graft or layered closure are billed together, Medicare's multiple-procedure rule pays the lower of the two at half: with a flap or graft that is the Mohs first stage, with a layered closure it is the closure. The same for an excision and its layered closure. That pairing is worked out case by case, so a change in how cases are closed moves it. Extra stages and add-on codes are paid in full.</p>
  <p>What the usual rates do not see (denials that stayed denied, later procedures of a day, units, underpayments) is carried code by code by comparing what payers actually allowed in the window with what this model says the same schedule was worth then.</p>
  <p>Collections are what the payers paid (second payers included, from the remittances) plus the share of patient balances you set as collected, by kind of payer; nothing is counted from Medicaid patients' balances. The payers' share is this clinician's own for each kind of payer, moved by how much more patients owe on a visit (copayments) than on a procedure.</p>
  <p>Contracts: "whole contract" sets every code at one share of Medicare, as a renegotiated schedule would, so codes now paid above it come down; "visit codes only" is the common carve-out ask; "every code raised" is an upper bound no payer signs.</p>
  ${d.next_year ? `<p>${esc(d.next_year)}: CMS's ${esc(d.next_year)} relative values, conversion factor and geographic indices for Kentucky and Indiana. The ${esc(d.rate_year)} amounts include a 1.0 floor on the work index that the ${esc(d.next_year)} file does not, so these assume Congress lets it lapse. Payers ticked as moving with Medicare follow its amounts; the rest keep their ${esc(d.rate_year)} rates. G2211 is replaced by modifier B1 (16% of the visit). The same-day rule (CMS-1848-F, 91 FR 71121) takes 20% off every service except the highest when an office visit billed with modifier 25 shares the day with a 0-day procedure (biopsies, shaves, Mohs first stages, intralesional injections); a visit with only frozen lesions, excisions or layered repairs (10-day codes) does not bring it in. It binds traditional Medicare; Medicare Advantage plans apply it only by contract. How it meets the 50% multiple-procedure cut is not settled: the page applies the 20% to what is left after it.</p>` : ''}
  <p>Not modelled: services billed under another clinician's number, no-shows, self-pay and cosmetic work (not on remittances), and where a payer's rate depends on the plan rather than the payer.</p>`;
}
