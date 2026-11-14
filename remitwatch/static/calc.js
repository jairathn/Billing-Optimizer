/* ==================================================================== Collections calculator
   One clinician's year (or the practice's average physician or APP): what the schedule brings in at each payer's own
   rates, what it would bring with the contracts that pay below Medicare raised to it, and what one more of anything
   is worth. The figures come from /api/activity/calculator (owner only); the arithmetic is CalcModel (calc_model.js),
   run in the page as the sliders move. */

const CALC_SLIDERS = [
  ['Clinic time', [
    ['days_per_week', 'Clinic days a week', 0, 6, 0.1, 'n1'],
    ['weeks_per_year', 'Weeks a year', 20, 52, 1, 'n0'],
  ]],
  ['Mohs', [
    ['mohs_cases', 'Mohs cases a day', 0, 30, 0.1, 'n1'],
    ['mohs_stages', 'Stages per case, on average', 1, 4, 0.01, 'n2'],
    ['mohs_hn', 'Cases on the head, neck, hands or feet (17311)', 0, 1, 0.01, 'pct'],
    ['fg_share', 'Flaps or grafts per 100 Mohs cases', 0, 1.5, 0.01, 'per100'],
    ['lay_mohs', 'Layered closures (intermediate or complex) per 100 Mohs cases', 0, 1.5, 0.01, 'per100'],
  ]],
  ['Excisions', [
    ['excisions', 'Excisions a day', 0, 15, 0.1, 'n1'],
    ['lay_exc', 'Layered closures per 100 excisions', 0, 1.5, 0.01, 'per100'],
  ]],
  ['Clinic visits', [
    ['visits', 'Visits a day', 0, 60, 0.5, 'n1'],
    ['new_share', 'New patients', 0, 1, 0.01, 'pct'],
    ['est_high', 'Established visits at 99214 or 99215', 0, 1, 0.01, 'pct'],
    ['new_high', 'New visits at 99204 or 99205', 0, 1, 0.01, 'pct'],
  ]],
  ['Procedures at visits', [
    ['bx_lesions', 'Biopsies a day (lesions)', 0, 40, 0.5, 'n1'],
    ['bx_per_patient', 'Biopsies per biopsied patient', 1, 5, 0.05, 'n2'],
    ['ak_lesions', 'AKs frozen a day (lesions)', 0, 150, 1, 'n0'],
    ['ak_per_patient', 'AKs per patient, under 15', 1, 14, 0.1, 'n1'],
    ['ak_15', 'AK patients with 15 or more (17004)', 0, 1, 0.01, 'pct'],
    ['benign', 'Patients with benign lesions frozen, a day', 0, 30, 0.5, 'n1'],
  ]],
  ['Everything else, against today', [
    ['other_radiation', 'Radiation', 0, 3, 0.05, 'pct', 'radiation'],
    ['other_skin', 'Skin substitutes and their application', 0, 3, 0.05, 'pct', 'skin'],
    ['other_path', 'Pathology billed under this number', 0, 3, 0.05, 'pct', 'path'],
    ['other_drug', 'Drugs', 0, 3, 0.05, 'pct', 'drug'],
    ['other_rest', 'Everything else (shaves, ED&C, injections, light...)', 0, 3, 0.05, 'pct', 'rest'],
  ]],
  ['Collections', [
    ['patient_collect', 'Patient balances collected', 0, 1, 0.01, 'pct'],
  ]],
];
const CALC_2027 = [
  ['bx_visit_share', 'Biopsied Medicare patients also billed a visit (modifier 25) that day', 0, 1, 0.01, 'pct'],
  ['mohs_visit_share', 'Mohs cases with a visit billed with modifier 25 the same day', 0, 1, 0.01, 'pct'],
];
const calcFmt = {n0: v => Math.round(v).toLocaleString(), n1: v => (Math.round(v * 10) / 10).toLocaleString(), n2: v => (Math.round(v * 100) / 100).toFixed(2),
                 pct: v => Math.round(v * 100) + '%', per100: v => Math.round(v * 100) + ' per 100'};
const KIND_LABEL = {medicare: 'Medicare', medicare_advantage: 'Medicare Advantage', commercial: 'Commercial', medicaid: 'Medicaid', supplement: 'Supplement', other: 'Other'};

async function calculatorTab(v){
  const cs = state.calc = Object.assign({provider: 'physicians', data: null, x: null, cal: null, now: null, target: ''}, state.calc || {});
  state.pv = state.pv || {};
  cs.el = v;
  if(pvLocked() && !state.ownerKey){ pvUnlockForm(v); return; }
  if(!cs.data || cs.data._for !== cs.provider){
    v.classList.add('busy');
    try {
      const d = await api('/api/activity/calculator?' + qs({provider: cs.provider}));
      if(state.tab !== 'calculator') return;
      if(!d.ready){ v.classList.remove('busy'); v.innerHTML = `<div class="panel"><h2>Collections calculator</h2><div class="note">${esc(d.message || 'The figures are not built yet.')}</div></div>`; return; }
      d._for = cs.provider;
      cs.data = d; cs.cal = CalcModel.calibrate(d); cs.x = CalcModel.defaults(d); cs.base = Object.assign({}, cs.x);
      cs.now = CalcModel.evaluate(d, cs.base, cs.cal);
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
  return w.id === 'physicians' || w.id === 'apps' ? `${w.name} (${w.clinicians} clinicians, their clinic days pooled)` : (w.name || 'Clinician');
}

function calcDraw(v){
  const cs = state.calc, d = cs.data, x = cs.x;
  const people = (d.clinicians || []).filter(c => c.tier === 'physician' || c.tier === 'nonphysician');
  const opt = (val, label) => `<option value="${esc(val)}" ${cs.provider === val ? 'selected' : ''}>${esc(label)}</option>`;
  const y27 = d.next_year;
  const months = `${d.from} to ${d.to}`;
  v.innerHTML = `
  <div class="panel">
    <h2 style="margin-bottom:4px">Collections calculator</h2>
    <div class="small muted">${esc(calcWho(d))}. Built from what was billed and allowed for services in ${esc(months)} (${(d.schedule.clinic_days||0).toLocaleString()} clinic days of 5 or more patients), priced at each payer's own ${esc(d.rate_year)} rates.</div>
    <div class="filters" style="margin-top:8px;gap:8px;flex-wrap:wrap">
      <div class="segs" role="group" aria-label="whose year">
        <button class="seg ${cs.provider==='physicians'?'on':''}" data-calc-who="physicians">Physician (average)</button>
        <button class="seg ${cs.provider==='apps'?'on':''}" data-calc-who="apps">APP (average)</button>
      </div>
      <select id="calcWho" aria-label="clinician">
        <option value="">One clinician…</option>
        <optgroup label="Physicians">${people.filter(c=>c.tier==='physician').map(c=>opt(c.provider_id, c.name)).join('')}</optgroup>
        <optgroup label="APPs">${people.filter(c=>c.tier==='nonphysician').map(c=>opt(c.provider_id, c.name)).join('')}</optgroup>
      </select>
      ${y27 ? `<div class="segs" role="group" aria-label="rates">
        <button class="seg ${x.year===d.rate_year?'on':''}" data-calc-year="${esc(d.rate_year)}">${esc(d.rate_year)} rates</button>
        <button class="seg ${x.year===y27?'on':''}" data-calc-year="${esc(y27)}">${esc(y27)} Medicare</button></div>` : ''}
      <button id="calcReset" class="ghost">Back to the schedule now</button>
    </div>
  </div>
  <div id="calcCards" class="grid kpis" style="margin-top:12px"></div>
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
  v.querySelectorAll('[data-calc-year]').forEach(b => b.onclick = () => { x.year = b.dataset.calcYear; calcDraw(v); });
  v.querySelector('#calcReset').onclick = () => { const keep = {year: x.year, corrected: x.corrected, target_pct: x.target_pct};
    cs.x = Object.assign({}, cs.base, keep); calcDraw(v); };
  calcInputs(v);
  calcUpdate();
  v.querySelector('#calcHowBody').innerHTML = calcHow(d);
}

function calcInputs(v){
  const cs = state.calc, d = cs.data, x = cs.x;
  const groups = CALC_SLIDERS.concat(x.year === d.next_year ? [[`Medicare's ${d.next_year} same-day rule`, CALC_2027]] : []);
  const otherNow = {};
  for (const [c, u] of Object.entries(d.levers.other.codes)) {
    const grp = (d.levers.other.groups || {})[c] || 'rest', cc = d.codes[c];
    otherNow[grp] = (otherNow[grp] || 0) + u * (cc && cc.units ? cc.allowed_cents / cc.units : 0);
  }
  const row = ([k, label, mn, mx, st, f, grp]) => {
    if (grp && !(otherNow[grp] > 0)) return '';
    const now = cs.base[k], val = x[k];
    const pctLike = f === 'pct' || f === 'per100';
    const hi = Math.max(mx, Math.ceil((now || 0) * 1.5 / st) * st);
    return `<div class="calc-row" data-k="${k}">
      <label for="cr_${k}">${esc(label)}</label>
      <div class="calc-ctl"><input type="range" id="cr_${k}" min="${mn}" max="${hi}" step="${st}" value="${val}">
        <input type="number" class="calc-num" aria-label="${esc(label)}" min="${mn}" step="${pctLike?1:st}" value="${pctLike ? Math.round(val*100) : (+val).toFixed(f==='n2'?2:f==='n1'?1:0)}">${f==='pct'?'<span class="small">%</span>':''}</div>
      <div class="small muted">now ${calcFmt[f](now || 0)}${grp ? ` of ${money(otherNow[grp])} allowed a clinic day` : ''}${k==='benign' ? ' · 17110 pays once per patient for 1 to 14 lesions' : ''}${k==='ak_lesions' ? ' · ' + calcFmt.n1(CalcModel.akPatients(d, x).patients) + ' patients' : ''}</div>
    </div>`;
  };
  const box = v.querySelector('#calcInputs');
  box.innerHTML = `<h2>The schedule</h2>
    <div class="small muted" style="margin-bottom:6px">Per clinic day unless it says otherwise. "Now" is ${esc(calcWho(d).split(' (')[0])}'s own average over the window.</div>
    ${groups.map(([title, rows]) => `<h3 class="calc-h">${esc(title)}</h3>${rows.map(row).join('')}`).join('')}
    <label class="small" style="display:block;margin-top:8px"><input type="checkbox" id="calcPath" ${x.include_path?'checked':''}> Add the pathology the biopsies bring the practice (read and billed by the practice's own reader)</label>
    ${x.year === d.next_year ? `<label class="small" style="display:block"><input type="checkbox" id="calcMA" ${x.ma_follows?'checked':''}> Medicare Advantage plans move with Medicare's ${esc(d.next_year)} amounts</label>
      <label class="small" style="display:block"><input type="checkbox" id="calcMASD" ${x.ma_same_day?'checked':''}> ...and apply its same-day rule (the rule binds traditional Medicare; a plan applies it only if its contract follows Medicare's payment rules)</label>
      <label class="small" style="display:block"><input type="checkbox" id="calcAdd" ${x.cut_add_ons?'checked':''}> The same-day rule cuts add-on codes too (extra Mohs stages, extra biopsies, 17003). The rule's words say every service but the highest; CMS has not said how add-ons are handled.</label>` : ''}`;
  box.querySelectorAll('.calc-row').forEach(r => {
    const k = r.dataset.k, def = groups.flatMap(g => g[1]).find(s => s[0] === k), pct = def[5] === 'pct' || def[5] === 'per100';
    const range = r.querySelector('input[type=range]'), num = r.querySelector('.calc-num');
    range.oninput = () => { x[k] = +range.value; num.value = pct ? Math.round(x[k] * 100) : range.value; calcSoon(); };
    num.oninput = () => { const n = parseFloat(num.value); if(!isFinite(n)) return; x[k] = pct ? n / 100 : n; range.value = x[k]; calcSoon(); };
  });
  box.querySelector('#calcPath').onchange = e => { x.include_path = e.target.checked; calcUpdate(); };
  const ma = box.querySelector('#calcMA'); if(ma) ma.onchange = e => { x.ma_follows = e.target.checked; calcUpdate(); };
  const masd = box.querySelector('#calcMASD'); if(masd) masd.onchange = e => { x.ma_same_day = e.target.checked; calcUpdate(); };
  const add = box.querySelector('#calcAdd'); if(add) add.onchange = e => { x.cut_add_ons = e.target.checked; calcUpdate(); };
}

let calcTimer = null;
function calcSoon(){ if(calcTimer) return; calcTimer = requestAnimationFrame(() => { calcTimer = null; calcUpdate(); }); }

function calcUpdate(){
  const cs = state.calc; if(!cs || !cs.data || !cs.el || !cs.el.isConnected) return;
  const d = cs.data, x = cs.x, v = cs.el;
  const nowX = Object.assign({}, cs.base, {year: x.year, corrected: {}, ma_follows: x.ma_follows, ma_same_day: x.ma_same_day, cut_add_ons: x.cut_add_ons,
                                           patient_collect: x.patient_collect, include_path: x.include_path,
                                           bx_visit_share: x.bx_visit_share, mohs_visit_share: x.mohs_visit_share});
  const now = CalcModel.evaluate(d, nowX, cs.cal);
  const plain = Object.assign({}, x, {corrected: {}});
  const plan = CalcModel.evaluate(d, plain, cs.cal);
  const fixed = CalcModel.evaluate(d, x, cs.cal);
  const nCorr = Object.values(x.corrected).filter(Boolean).length;
  const delta = (a, b) => { const dv = a - b; return Math.abs(dv) < 50 ? '' : `<span class="${dv>0?'ok':'sev-high'}">${dv>0?'+':'−'}${money(Math.abs(dv))}</span>`; };
  const per = r => r.daysYear ? `${money(r.totalCollections / Math.max(1, r.daysYear / Math.max(0.1, x.days_per_week)))} a week · ${money(r.totalCollections / r.daysYear)} a clinic day` : '';
  v.querySelector('#calcCards').innerHTML = `
    <div class="kpi"><div class="l">Now, a year</div><div class="v">${money(now.totalCollections)}</div><div class="s">the schedule as it is, ${esc(x.year)} ${x.year===d.rate_year?'rates':'Medicare'}<br>${per(now)}</div></div>
    <div class="kpi"><div class="l">Your plan, a year</div><div class="v">${money(plan.totalCollections)}</div><div class="s">${delta(plan.totalCollections, now.totalCollections) || 'no change yet'}<br>${per(plan)}</div></div>
    <div class="kpi"><div class="l">Plan, with ${nCorr ? nCorr + ' contract' + (nCorr>1?'s':'') + ' corrected' : 'contracts corrected'}</div><div class="v">${money(fixed.totalCollections)}</div><div class="s">${nCorr ? delta(fixed.totalCollections, plan.totalCollections) + ' from the contracts' : 'tick contracts on the right'}<br>${per(fixed)}</div></div>`;
  // where the money comes from
  const rows = CalcModel.LINES.map(([k, label]) => [k, label, (now.byLine[k] || {}).collections || 0, (fixed.byLine[k] || {}).collections || 0]).filter(r => r[2] || r[3]);
  const extra = [];
  if(fixed.sameDay.collections || now.sameDay.collections) extra.push(['', `Medicare's ${d.next_year} same-day rule`, -now.sameDay.collections, -fixed.sameDay.collections]);
  if(x.include_path) extra.push(['', 'Pathology the biopsies bring the practice', now.pathCollections, fixed.pathCollections]);
  v.querySelector('#calcLines').innerHTML = `<h2>Where it comes from, a year</h2>
    <table><thead><tr><th>Service line</th><th class="num">Now</th><th class="num">Plan${nCorr?' + contracts':''}</th><th class="num">Change</th></tr></thead><tbody>
    ${rows.concat(extra).map(([k, label, a, b]) => `<tr><td>${esc(label)}</td><td class="num">${money(a)}</td><td class="num">${money(b)}</td><td class="num">${delta(b, a)}</td></tr>`).join('')}
    <tr><td><b>Collections</b></td><td class="num"><b>${money(now.totalCollections)}</b></td><td class="num"><b>${money(fixed.totalCollections)}</b></td><td class="num">${delta(fixed.totalCollections, now.totalCollections)}</td></tr>
    <tr class="small muted"><td>Allowed by payers (before patient balances)</td><td class="num">${money(now.allowed)}</td><td class="num">${money(fixed.allowed)}</td><td></td></tr>
    </tbody></table>
    <div class="small muted" style="margin-top:6px">In ${esc(d.from)} to ${esc(d.to)} payers allowed ${money(d.actual.allowed_cents)} for this ${d.who.clinicians>1?'group':'clinician'} and paid ${money(d.actual.paid_cents)} (second payers included).</div>`;
  calcPayers(v, plain);
  calcMoves(v);
}

function calcPayers(v, plain){
  const cs = state.calc, d = cs.data, x = cs.x;
  const U = CalcModel.units(d, plain), Y = x.year;
  const base = CalcModel.evaluate(d, plain, cs.cal);
  const list = Object.entries(d.payers).map(([g, p]) => {
    let pay = 0, med = 0;
    for(const [c, n] of Object.entries(U)){
      const s = (CalcModel.mixOf(d, c).find(e => e[0] === g) || [0, 0])[1];
      const M = (d.medicare[c] || {})[Y], r = CalcModel.price(d, plain, c, g, Y, false);
      if(s && M && r != null){ pay += n * s * r; med += n * s * M; }
    }
    const al = (base.byPayer[g] || {}).allowed || 0;
    let own = 0, all = 0;            // how much of this payer's money is priced on its own rates (or Medicare's schedule)
    for(const [c, n] of Object.entries(U)){
      const s = (CalcModel.mixOf(d, c).find(e => e[0] === g) || [0, 0])[1], row = (d.rates[c] || {})[g];
      const r = row && row[d.rate_year];
      if(s && r){ all += n * s * r; if(['own', 'medicare_schedule'].includes(row.how)) own += n * s * r; }
    }
    let up = 0;
    if(p.correctable && al > 0){
      const one = Object.assign({}, plain, {corrected: {[g]: true}});
      up = CalcModel.evaluate(d, one, cs.cal).totalCollections - base.totalCollections;
    }
    return {g, kind: p.kind, correctable: p.correctable, pct: med ? pay / med : null, allowed: al, up, est: all > 0 && own / all < 0.5};
  }).filter(r => r.allowed > 0).sort((a, b) => b.allowed - a.allowed);
  const total = list.reduce((t, r) => t + r.allowed, 0) || 1;
  const big = list.filter(r => r.allowed / total >= 0.005 || x.corrected[r.g]), small = list.filter(r => !big.includes(r));
  const payerRow = r => `<tr><td>${r.correctable ? `<input type="checkbox" data-calc-fix="${esc(r.g)}" ${x.corrected[r.g]?'checked':''} aria-label="correct ${esc(r.g)}">` : ''}</td>
      <td>${esc(r.g)}<div class="small muted">${esc(KIND_LABEL[r.kind] || r.kind)}${r.kind==='medicaid'?' · rates set by the state program; rarely moved':''}</div></td>
      <td class="num">${r.allowed / total >= 0.005 ? Math.round(100 * r.allowed / total) + '%' : '<1%'}</td>
      <td class="num ${r.pct!=null && r.pct < x.target_pct - 0.005 ? 'sev-high' : ''}">${r.pct==null?'–':Math.round(r.pct*100)+'%'}${r.est?' <span class="muted">est.</span>':''}</td>
      <td class="num">${r.correctable ? (r.up > 50 ? '+' + money(r.up) : '–') : ''}</td></tr>`;
  const box = v.querySelector('#calcPayers');
  box.innerHTML = `<h2>Contracts</h2>
    <div class="small muted">What each payer pays for this schedule, as a share of Medicare's ${esc(Y)} amount for the same codes. Tick a contract to see it raised to
      <input type="number" id="calcTarget" min="50" max="200" step="5" value="${Math.round(x.target_pct*100)}" style="width:64px" aria-label="target share of Medicare">% of Medicare on every code it pays below that.</div>
    <div class="actions" style="margin:6px 0"><button id="calcAllBelow">Tick every commercial and Medicare Advantage contract below it</button> <button id="calcNone" class="ghost">Clear</button></div>
    <table><thead><tr><th></th><th>Payer</th><th class="num">Share</th><th class="num">Pays, vs Medicare</th><th class="num">Corrected, a year</th></tr></thead><tbody>
    ${big.map(payerRow).join('')}
    </tbody></table>
    ${small.length ? `<details style="margin-top:6px"><summary class="small">${small.length} payers with under half a percent of the money each</summary>
      <table><tbody>${small.map(payerRow).join('')}</tbody></table></details>` : ''}
    <div class="small muted" style="margin-top:6px">est. = most of this payer's rates for these codes are not in the remittances and are estimated from its percentage of Medicare.</div>`;
  box.querySelectorAll('[data-calc-fix]').forEach(cb => cb.onchange = () => { x.corrected[cb.dataset.calcFix] = cb.checked; calcUpdate(); });
  box.querySelector('#calcTarget').onchange = e => { const n = parseFloat(e.target.value); if(isFinite(n) && n > 0){ x.target_pct = n / 100; calcUpdate(); } };
  box.querySelector('#calcAllBelow').onclick = () => { for(const r of list) if(r.correctable && r.kind !== 'medicaid' && r.pct != null && r.pct < x.target_pct - 0.005) x.corrected[r.g] = true; calcUpdate(); };
  box.querySelector('#calcNone').onclick = () => { x.corrected = {}; calcUpdate(); };
}

function calcMoves(v){
  const cs = state.calc, d = cs.data, x = cs.x;
  const moves = CalcModel.marginal(d, x, cs.cal);
  v.querySelector('#calcMoves').innerHTML = `<h2>What one more is worth</h2>
    <div class="small muted">Collections a year, with the rest of the plan as it stands and the contracts as ticked.</div>
    <table><tbody>${moves.map(m => `<tr><td>${esc(m.label)}</td><td class="num">${m.value >= 0 ? '+' : '−'}${money(Math.abs(m.value))}</td></tr>`).join('')}</tbody></table>`;
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
  if(!isFinite(t) || t <= 0){ out.innerHTML = '<span class="muted">Type a yearly figure to see, lever by lever, how much more it takes with everything else held where the plan has it.</span>'; return; }
  const res = CalcModel.goalSeek(d, x, t * 100, cs.cal);
  const gap = res.length ? res[0].gap : 0;
  if(gap <= 0){ out.innerHTML = `<p class="ok">The plan already reaches it, with ${money(-gap)} to spare.</p>`; return; }
  const dpw = Math.max(0.5, x.days_per_week);
  const say = m => {
    if(m.needed == null) return 'does not move collections';
    const n = m.needed;
    if(m.key === 'days_per_week') return `${(n * 0.5).toFixed(1)} more clinic days a week`;
    if(m.key === 'est_high'){ const pts = n * 10, cur = x[m.key] * 100; return cur + pts > 100 ? 'not enough on its own (more than all of them)' : `${Math.round(cur)}% → ${Math.round(cur + pts)}%`; }
    if(m.key === 'fg_share'){ const pts = n * 10, cur = x[m.key] * 100; return cur + pts > 150 ? 'not enough on its own' : `${Math.round(cur)} → ${Math.round(cur + pts)} per 100 cases`; }
    if(m.key === 'mohs_stages') return `${x.mohs_stages.toFixed(2)} → ${(x.mohs_stages + n * 0.1).toFixed(2)} stages per case`;
    if(m.key === 'ak_lesions') return `${Math.round(n * 5)} more a week (${(n * 5 / dpw).toFixed(1)} a day)`;
    return `${n.toFixed(1)} more a week (${(n / dpw).toFixed(1)} a day)`;
  };
  out.innerHTML = `<p>${money(gap)} a year short. Any one of these closes it on its own:</p>
    <table><tbody>${res.filter(m => m.needed != null).map(m => `<tr><td>${esc(m.name)}</td><td class="num">${esc(say(m))}</td></tr>`).join('')}</tbody></table>`;
}

function calcHow(d){
  const src = d.rate_sources || {}, n = Object.values(src).reduce((a, b) => a + b, 0) || 1;
  const pct = k => Math.round(100 * (src[k] || 0) / n);
  return `<p>The schedule is turned into billed codes with this clinician's own mix: which Mohs codes, which flaps and grafts, which excision sizes, which visit levels. Each code is priced at every payer's own usual allowed amount in the payer mix of that service line, as this clinician's (or, where they bill too little of it, the group's) remittances show it.</p>
  <p>Where a payer's rate for a code is not in the remittances, the code is priced at that payer's own percentage of Medicare for the same kind of service times Medicare's published amount (${pct('payer_pct_of_medicare')}% of payer-and-code pairs, mostly rare codes); Medicare itself is priced from CMS's fee schedule for Kentucky and Indiana, weighted by where the clinic days were.</p>
  <p>When a Mohs first stage and a flap, graft or layered closure are billed together, Medicare's multiple-procedure rule pays the lower of the two at half; the same for an excision and its layered closure. That pairing is worked out case by case, so a change in how cases are closed moves it. Extra stages and add-on codes are paid in full.</p>
  <p>What the usual rates do not see (denials that stayed denied, later procedures of a day, units, underpayments) is carried code by code by comparing what payers actually allowed in the window with what this model says the same schedule was worth then. With the schedule as it is and no contract corrected, the page gives back what was allowed.</p>
  <p>Collections are what the payers paid (second payers included, from the remittances) plus the share of patient balances you set as collected; nothing is counted from Medicaid patients' balances. The payers' share is this clinician's own for each kind of payer, moved by how much more patients owe on a visit (copayments) than on a procedure. The 835s show what a patient owed, not what the patient paid.</p>
  ${d.next_year ? `<p>${esc(d.next_year)} Medicare: CMS's ${esc(d.next_year)} relative values, conversion factor and geographic indices for Kentucky and Indiana. The ${esc(d.rate_year)} amounts include a 1.0 floor on the work index that the ${esc(d.next_year)} file does not, so these assume Congress lets it lapse. G2211 is replaced by modifier B1 (16% of the visit). The same-day rule (CMS-1848-F, 91 FR 71121) takes 20% off every service except the highest when an office visit billed with modifier 25 shares the day with a 0-day procedure (biopsies, shaves, Mohs first stages, intralesional injections); a visit with only frozen lesions, excisions or layered repairs (10-day codes) does not bring it in. It binds traditional Medicare; Medicare Advantage plans apply it only by contract, so that is a switch. How it meets the 50% multiple-procedure cut is not settled: the page applies the 20% to what is left after it. Commercial and Medicaid contracts are left at their ${esc(d.rate_year)} rates.</p>` : ''}
  <p>Not modelled: services billed under another clinician's number, a payer changing its contract mid-year, no-shows, and where a payer's rate depends on the plan rather than the payer.</p>`;
}
