/* ==================================================================== Strategy lab, practice figures, provider sheet
   Three more views of the Calculator tab, for the owner. The lab starts from the calculator's plan and stacks
   strategies (lab_model.js); the practice figures (minutes, costs, pay plans) are saved to the site
   (/api/activity/assumptions); the provider sheet turns a pay target into what a clinic day has to hold, for one
   clinician, printable. */

const LAB_GROUPS = [
  ['Money already earned', ['appeals', 'vendor_fees', 'collect', 'b1', 'contract_month']],
  ['Staffing and clinic time', ['app_shift', 'add_session']],
  ['Where cases are done', ['asc']],
  ['Timing of procedures', ['stage', 'biopsy_return']],
];
const LAB_TIER = {low: ['routine', 'ok'], medium: ['check first', 'sev-medium'], high: ['audit exposure', 'sev-high']};

function calcViews(v){
  const cs = state.calc;
  const views = [['calc', 'Calculator'], ['lab', 'Strategy lab'], ['figures', 'Practice figures'], ['sheet', 'Provider sheet']];
  return `<div class="segs" role="tablist" aria-label="calculator views" style="margin-top:10px">${views.map(([k, l]) =>
    `<button class="seg ${cs.view===k?'on':''}" data-calc-view="${k}" role="tab" aria-selected="${cs.view===k}">${esc(l)}</button>`).join('')}</div>`;
}

async function labAssumptions(force){
  const cs = state.calc;
  if(cs.A && !force) return cs.A;
  try { const r = await api('/api/activity/assumptions'); cs.A = LabModel.filled(r.assumptions); cs.Asaved = r.assumptions ? r.assumptions.saved_at : null; }
  catch(e){ cs.A = LabModel.filled(null); cs.Aerror = e.message; }
  cs.lab = cs.lab || {};
  return cs.A;
}

async function labSave(){
  const cs = state.calc;
  const r = await api('/api/activity/assumptions', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(cs.A)});
  cs.Asaved = r.saved_at;
  return r;
}

const labMoney = c => c == null ? '–' : moneyK(c);
const labSigned = c => !c || Math.abs(c) < 500 ? '<span class="muted">–</span>' : `<span class="${c>0?'ok':'sev-high'}">${c>0?'+':'−'}${moneyK(Math.abs(c))}</span>`;
const LAB_NEED_LABEL = Object.fromEntries([...LabModel.MINUTES.map(([k, l]) => ['minutes.' + k, 'minutes: ' + l.toLowerCase()]),
                                           ...LabModel.COSTS.map(([k, l]) => ['costs.' + k, 'cost: ' + l.toLowerCase()])]);

// ------------------------------------------------------------------ the lab
async function labDraw(v){
  const cs = state.calc, d = cs.data, x = cs.x;
  await labAssumptions();
  const lab = cs.lab = cs.lab || {};
  lab.chosen = lab.chosen || {};
  v.querySelector('#calcBody').innerHTML = `
    <div class="panel"><h2>Strategy lab</h2>
      <div class="small muted">Starts from the plan on the Calculator view (${esc(d.who.name)}, ${esc(x.year)}${x.year===d.next_year?' Medicare rules':' rates'}). Tick strategies to stack them; each line shows what it adds on top of the ones above it. Minutes and costs come from Practice figures; where one is missing the line says so instead of guessing.</div>
      <div id="labSummary" style="margin-top:8px"></div></div>
    ${LAB_GROUPS.map(([title, ids]) => `<div class="panel"><h2>${esc(title)}</h2>${ids.map(id => labCard(LabModel.STRATEGIES.find(s => s.id === id))).join('')}</div>`).join('')}
    <div class="panel" id="labMinutes"></div>
    <div class="panel" id="labScenarios"></div>`;
  const body = v.querySelector('#calcBody');
  body.querySelectorAll('[data-lab-on]').forEach(cb => cb.onchange = () => { const id = cb.dataset.labOn; lab.chosen[id] = Object.assign(lab.chosen[id] || {}, {on: cb.checked}); labUpdate(v); });
  body.querySelectorAll('[data-lab-p]').forEach(inp => {
    const [id, key, fmt] = inp.dataset.labP.split('|');
    const set = () => {
      let val = inp.type === 'checkbox' ? (inp.checked ? 1 : 0) : parseFloat(inp.value);
      if(!isFinite(val)) return;
      if(fmt === 'pct' && inp.type !== 'range') val = val / 100;
      const c = lab.chosen[id] = lab.chosen[id] || {on: false};
      c.params = Object.assign(c.params || {}, {[key]: val});
      const twin = body.querySelector(`[data-lab-twin="${id}|${key}"]`);
      if(twin && inp.type === 'range') twin.value = fmt === 'pct' ? Math.round(val * 100) : val;
      if(twin && inp.type !== 'range'){ const r = body.querySelector(`input[type=range][data-lab-p="${id}|${key}|${fmt}"]`); if(r) r.value = val; }
      labSoon(v);
    };
    inp.oninput = set; inp.onchange = set;
  });
  labUpdate(v);
}

function labCard(S){
  const cs = state.calc, d = cs.data, c = (cs.lab.chosen || {})[S.id] || {}, p = Object.assign(LabModel.paramDefaults(S), c.params || {});
  const [tier, cls] = LAB_TIER[S.tier] || ['', ''];
  const off = S.only === 'next' && cs.x.year !== d.next_year;
  const ctl = ([key, label, mn, mx, st, fmt]) => {
    const val = p[key];
    if(fmt === 'bool') return `<label class="small" style="display:block"><input type="checkbox" data-lab-p="${S.id}|${key}|bool" ${val?'checked':''}> ${esc(label)}</label>`;
    const shown = fmt === 'pct' ? Math.round(val * 100) : val;
    return `<div class="calc-row"><label>${esc(label)}</label><div class="calc-ctl">
      <input type="range" min="${mn}" max="${mx}" step="${st}" value="${val}" data-lab-p="${S.id}|${key}|${fmt}">
      <input type="number" class="calc-num" value="${shown}" data-lab-p="${S.id}|${key}|${fmt}" data-lab-twin="${S.id}|${key}">${fmt==='pct'?'<span class="small">%</span>':''}</div></div>`;
  };
  return `<div class="lab-card" data-lab-card="${S.id}">
    <label class="lab-head"><input type="checkbox" data-lab-on="${S.id}" ${c.on?'checked':''} ${off?'disabled':''}> <b>${esc(S.title)}</b>
      <span class="chip ${cls}">${esc(tier)}</span></label>
    <div class="small muted">${esc(S.why)}${off ? ' Switch the Calculator to Plan ' + esc(d.next_year) + ' to use it.' : ''}</div>
    <div class="lab-impact small" data-lab-impact="${S.id}"></div>
    <details><summary class="small">Settings${S.compliance ? ' and compliance' : ''}</summary>
      ${S.params.map(ctl).join('')}
      ${S.compliance ? `<div class="small ${S.tier==='high'?'sev-high':''}" style="margin-top:6px">${esc(S.compliance)}</div>` : ''}
    </details></div>`;
}

let labTimer = null;
function labSoon(v){ if(labTimer) return; labTimer = requestAnimationFrame(() => { labTimer = null; labUpdate(v); }); }

function labUpdate(v){
  const cs = state.calc, d = cs.data, x = cs.x, lab = cs.lab;
  const body = v.querySelector('#calcBody'); if(!body || !body.querySelector('#labSummary')) return;
  const r = LabModel.run(d, x, cs.A, lab.chosen, cs.cal);
  lab.last = r;
  for(const S of LabModel.STRATEGIES){
    const el = body.querySelector(`[data-lab-impact="${S.id}"]`); if(!el) continue;
    const st = r.steps.find(s => s.id === S.id);
    if(!st){ el.innerHTML = ''; continue; }
    if(st.skipped){ el.innerHTML = `<span class="muted">${esc(st.skipped)}</span>`; continue; }
    const bits = [`collections ${labSigned(st.collections)} a year`];
    if(Math.abs(st.cost) >= 500) bits.push(`costs ${st.cost > 0 ? '+' : '−'}${moneyK(Math.abs(st.cost))}`);
    if(Math.abs(st.collections - st.cost) >= 500 && Math.abs(st.cost) >= 500) bits.push(`net ${labSigned(st.collections - st.cost)}`);
    if(Math.abs(st.encountersDay) >= 0.01) bits.push(`${st.encountersDay > 0 ? '+' : ''}${st.encountersDay.toFixed(2)} visits a clinic day`);
    if(Math.abs(st.minutesDay) >= 0.5) bits.push(`${st.minutesDay > 0 ? '+' : ''}${st.minutesDay.toFixed(0)} clinician minutes a day`);
    if(st.oneTime) bits.push(`one-time ${moneyK(st.oneTime)}`);
    el.innerHTML = bits.join(' · ') + (st.notes.length ? `<div class="muted">${st.notes.map(esc).join(' ')}</div>` : '')
      + (st.needs.length ? `<div class="sev-medium">Not entered yet: ${st.needs.map(n => esc(LAB_NEED_LABEL[n] || n)).join('; ')}. <a href="#" data-goto-figures>Practice figures</a></div>` : '');
  }
  const R = r.result, B = r.base;
  const costKnown = !R.costs.missing.length;
  const comp = labPayFor(d, R);
  body.querySelector('#labSummary').innerHTML = `<table class="calc-fall"><tbody>
    <tr><td>The calculator's plan</td><td class="num">${moneyK(B.collections)}</td></tr>
    ${r.steps.filter(s => !s.skipped).map(s => `<tr><td>${esc(s.title)}</td><td class="num">${labSigned(s.collections)}</td></tr>`).join('')}
    <tr class="calc-total"><td><b>Collections a year with these strategies</b>${R.practiceOnly ? `<div class="small muted">of which ${moneyK(R.practiceOnly)} is the practice's (APP visits, vendor fees), not this clinician's production</div>` : ''}</td><td class="num"><b style="font-size:20px">${moneyK(R.collections)}</b></td></tr>
    <tr><td>Costs a year${costKnown ? '' : ' <span class="small sev-medium">(some not entered)</span>'}</td><td class="num">${R.cost ? moneyK(R.cost) : '<span class="muted">not entered</span>'}</td></tr>
    ${R.cost ? `<tr><td><b>Left after costs</b></td><td class="num"><b>${moneyK(R.collections - R.cost)}</b> <span class="small muted">(${labSigned((R.collections - R.cost) - (B.collections - B.cost))})</span></td></tr>` : ''}
    ${R.oneTime ? `<tr><td>One-time cash (appeals)</td><td class="num">${moneyK(R.oneTime)}</td></tr>` : ''}
    <tr><td>Clinician minutes a clinic day${r.capacity ? '' : ' <span class="small muted">(enter the minutes in Practice figures)</span>'}</td>
      <td class="num">${R.minutesDay ? R.minutesDay.toFixed(0) : '–'}${r.capacity ? ` of ${r.capacity.available} <span class="${r.capacity.share > 1 ? 'sev-high' : 'muted'}">(${Math.round(r.capacity.share * 100)}%)</span>` : ''}</td></tr>
    <tr><td>Work RVUs a year</td><td class="num">${Math.round(R.wrvu).toLocaleString()}${Math.abs(R.wrvu - B.wrvu) >= 1 ? ` <span class="small muted">(${R.wrvu > B.wrvu ? '+' : ''}${Math.round(R.wrvu - B.wrvu).toLocaleString()})</span>` : ''}</td></tr>
    ${comp ? `<tr><td>Pay under ${esc(comp.label)}</td><td class="num">${moneyK(comp.pay)}</td></tr>` : ''}
    </tbody></table>
    ${r.capacity && r.capacity.share > 1 ? `<div class="small sev-high">The plan needs more clinician minutes than a clinic day holds. Either the minutes entered are hands-on minutes the clinician can overlap across rooms (then raise the day's minutes), or the plan does not fit.</div>` : ''}`;
  body.querySelectorAll('[data-goto-figures]').forEach(a => a.onclick = e => { e.preventDefault(); cs.view = 'figures'; calcDraw(cs.el); });
  // what earns the most per minute
  const pm = r.perMinute.filter(m => m.value != null);
  body.querySelector('#labMinutes').innerHTML = `<h2>What a clinician minute brings in</h2>
    <div class="small muted">Collections per hands-on minute, at this plan's prices and the minutes in Practice figures. The same day spent on the top rows brings in more; clinical need decides what is done.</div>
    <table><thead><tr><th>Work</th><th class="num">Collections each</th><th class="num">Minutes</th><th class="num">Per minute</th></tr></thead><tbody>
    ${pm.sort((a, b) => (b.perMinute || 0) - (a.perMinute || 0)).map(m => `<tr><td>${esc(m.label)}</td><td class="num">${money(m.value)}</td><td class="num">${m.minutes == null ? '<span class="muted">not entered</span>' : m.minutes.toFixed(0)}</td><td class="num">${m.perMinute == null ? '–' : money(m.perMinute)}</td></tr>`).join('')}
    </tbody></table>`;
  labScenarios(v);
}

function labPayFor(d, R){
  const cs = state.calc, plan = (cs.A.comp || {})[d.who.id];
  if(!plan || !plan.type) return null;
  const p = LabModel.pay(plan, R.collections - (R.practiceOnly || 0), R.wrvu);
  if(p == null) return null;
  return {pay: p, label: (LabModel.COMP_TYPES.find(t => t[0] === plan.type) || ['', 'the pay plan'])[1].toLowerCase()};
}

// ------------------------------------------------------------------ scenarios: saved with the practice figures
function labScenarios(v){
  const cs = state.calc, d = cs.data, A = cs.A, box = v.querySelector('#labScenarios');
  const mine = (A.scenarios || []).filter(s => s.who === d.who.id);
  const rows = mine.map(s => {
    const x = Object.assign(CalcModel.defaults(d), s.x || {});
    const r = LabModel.run(d, x, A, s.chosen || {}, cs.cal);
    return {s, r};
  });
  const cur = cs.lab.last;
  box.innerHTML = `<h2>Scenarios</h2>
    <div class="small muted">Save the calculator's plan and the ticked strategies under a name, then compare. Saved with the practice figures, for the owner only.</div>
    <div class="filters" style="margin:6px 0"><input id="labName" placeholder="Name this scenario" style="min-width:220px"> <button id="labSaveScen">Save</button> <span class="small muted" id="labSaved"></span></div>
    ${rows.length ? `<table><thead><tr><th>Scenario</th><th class="num">Collections</th><th class="num">Costs</th><th class="num">After costs</th><th class="num">Minutes a day</th><th></th></tr></thead><tbody>
      <tr><td><i>Now on screen</i></td><td class="num">${moneyK(cur.result.collections)}</td><td class="num">${cur.result.cost ? moneyK(cur.result.cost) : '–'}</td><td class="num">${cur.result.cost ? moneyK(cur.result.collections - cur.result.cost) : '–'}</td><td class="num">${cur.result.minutesDay ? cur.result.minutesDay.toFixed(0) : '–'}</td><td></td></tr>
      ${rows.map(({s, r}, i) => `<tr><td>${esc(s.name)}<div class="small muted">${esc((s.saved_at || '').slice(0, 10))} · ${esc((s.x || {}).year || '')}</div></td><td class="num">${moneyK(r.result.collections)}</td><td class="num">${r.result.cost ? moneyK(r.result.cost) : '–'}</td>
        <td class="num">${r.result.cost ? moneyK(r.result.collections - r.result.cost) : '–'}</td><td class="num">${r.result.minutesDay ? r.result.minutesDay.toFixed(0) : '–'}</td>
        <td class="num"><button data-lab-load="${i}">Open</button> <button class="ghost" data-lab-del="${i}">Delete</button></td></tr>`).join('')}
      </tbody></table>` : ''}`;
  box.querySelector('#labSaveScen').onclick = async () => {
    const name = box.querySelector('#labName').value.trim(); if(!name) return;
    const keep = JSON.parse(JSON.stringify(cs.x));
    A.scenarios = (A.scenarios || []).filter(s => !(s.who === d.who.id && s.name === name));
    A.scenarios.push({name, who: d.who.id, x: keep, chosen: JSON.parse(JSON.stringify(cs.lab.chosen || {})), saved_at: new Date().toISOString()});
    try { await labSave(); labScenarios(v); } catch(e){ box.querySelector('#labSaved').textContent = e.message; }
  };
  box.querySelectorAll('[data-lab-load]').forEach(b => b.onclick = () => { const s = mine[+b.dataset.labLoad];
    cs.x = Object.assign(CalcModel.defaults(d), JSON.parse(JSON.stringify(s.x))); cs.lab.chosen = JSON.parse(JSON.stringify(s.chosen || {})); calcDraw(cs.el); });
  box.querySelectorAll('[data-lab-del]').forEach(b => b.onclick = async () => { const s = mine[+b.dataset.labDel];
    A.scenarios = A.scenarios.filter(t => t !== s); try { await labSave(); } catch(e){} labScenarios(v); });
}

// ------------------------------------------------------------------ practice figures
async function figuresDraw(v){
  const cs = state.calc, d = cs.data;
  const A = await labAssumptions();
  const field = (group, key, label, unit) => `<div class="calc-row"><label for="f_${group}_${key}">${esc(label)}</label>
    <div class="calc-ctl"><input type="number" id="f_${group}_${key}" data-fig="${group}.${key}" min="0" step="any" placeholder="not entered" value="${(group === 'session' ? A.session_minutes : A[group][key]) == null ? '' : (group === 'session' ? A.session_minutes : A[group][key])}" style="width:120px"> <span class="small muted">${esc(unit)}</span></div></div>`;
  const people = (d.clinicians || []).map(c => [c.provider_id, c.name]).concat(Object.entries(ROLE_LABEL).map(([k, l]) => [k, l + ' (average)']));
  const comp = A.comp || {};
  const compRow = ([id, name]) => { const p = comp[id] || {};
    return `<tr data-comp="${esc(id)}"><td>${esc(name)}</td><td><select data-c="type"><option value="">no plan entered</option>${LabModel.COMP_TYPES.map(([k, l]) => `<option value="${k}" ${p.type===k?'selected':''}>${esc(l)}</option>`).join('')}</select></td>
      <td><input type="number" data-c="pct" step="any" placeholder="share, %" value="${p.pct != null ? Math.round(p.pct * 1000) / 10 : ''}" style="width:90px" title="share of collections, %"></td>
      <td><input type="number" data-c="per_wrvu" step="any" placeholder="$ per wRVU" value="${p.per_wrvu ?? ''}" style="width:90px"></td>
      <td><input type="number" data-c="base" step="any" placeholder="base $" value="${p.base ?? ''}" style="width:100px"></td>
      <td><input type="number" data-c="threshold" step="any" placeholder="threshold $" value="${p.threshold ?? ''}" style="width:110px"></td>
      <td><input type="number" data-c="above_pct" step="any" placeholder="above, %" value="${p.above_pct != null ? Math.round(p.above_pct * 1000) / 10 : ''}" style="width:80px"></td></tr>`; };
  v.querySelector('#calcBody').innerHTML = `
    <div class="panel"><h2>Practice figures</h2>
      <div class="small muted">The practice's own numbers, used by the Strategy lab and the Provider sheet. Leave a box empty until you have the figure: anything that depends on it is shown as not entered, never guessed. Saved to the site for the owner only${cs.Asaved ? `; last saved ${esc(cs.Asaved.replace('T', ' ').slice(0, 16))}` : ''}.</div>
      <div class="actions" style="margin-top:8px"><button class="primary" id="figSave">Save</button> <span id="figMsg" class="small"></span></div></div>
    <div class="grid two">
      <div class="panel"><h2>Clinician minutes</h2><div class="small muted">Hands-on minutes of the clinician for each piece of work, not room time. A Mohs surgeon running several rooms counts only the minutes spent with each patient and at the microscope.</div>
        ${field('session', 'minutes', 'Minutes the clinician has in a clinic day', 'minutes')}
        ${LabModel.MINUTES.map(([k, l]) => field('minutes', k, l, 'minutes')).join('')}</div>
      <div class="panel"><h2>Costs</h2><div class="small muted">Dollars. A clinic day's fixed cost is what runs whether patients come or not; the rest are per piece of work.</div>
        ${LabModel.COSTS.map(([k, l]) => field('costs', k, l, 'dollars')).join('')}</div>
    </div>
    <div class="panel"><h2>Pay plans</h2><div class="small muted">How each clinician is paid on their own production. Under the Stark rules a productivity plan may count services the clinician performs personally (and incident-to services), not the pathology, radiation or drugs they refer; the calculator's production figure already leaves those out.</div>
      <table><thead><tr><th>Clinician</th><th>Plan</th><th>Share of collections</th><th>Per wRVU</th><th>Base</th><th>Threshold</th><th>Share above</th></tr></thead><tbody>${people.map(compRow).join('')}</tbody></table></div>`;
  const body = v.querySelector('#calcBody');
  body.querySelector('#figSave').onclick = async () => {
    const A2 = LabModel.filled(null);
    A2.scenarios = A.scenarios || [];
    body.querySelectorAll('[data-fig]').forEach(inp => {
      const [g, k] = inp.dataset.fig.split('.'), n = inp.value === '' ? null : +inp.value;
      if(g === 'session') A2.session_minutes = n; else A2[g][k] = n;
    });
    body.querySelectorAll('tr[data-comp]').forEach(tr => {
      const val = s => { const i = tr.querySelector(`[data-c="${s}"]`); return i && i.value !== '' ? +i.value : null; };
      const type = tr.querySelector('[data-c="type"]').value;
      if(type) A2.comp[tr.dataset.comp] = {type, pct: val('pct') == null ? null : val('pct') / 100, per_wrvu: val('per_wrvu'), base: val('base'),
                                           threshold: val('threshold'), above_pct: val('above_pct') == null ? null : val('above_pct') / 100};
    });
    cs.A = A2;
    const msg = body.querySelector('#figMsg');
    try { await labSave(); msg.textContent = 'Saved.'; msg.className = 'small ok'; } catch(e){ msg.textContent = e.message; msg.className = 'small sev-high'; }
  };
}

// ------------------------------------------------------------------ provider sheet
async function sheetDraw(v){
  const cs = state.calc, d = cs.data, x = cs.x;
  const A = await labAssumptions();
  const plan = (A.comp || {})[d.who.id] || null;
  const now = CalcModel.evaluate(d, calcWith(cs.base, x, {year: x.year}), cs.cal);
  const planned = CalcModel.evaluate(d, x, cs.cal);
  const wNow = LabModel.wrvus(d, calcWith(cs.base, x, {year: x.year})), wPlan = LabModel.wrvus(d, x);
  const target = cs.sheetTarget || '';
  v.querySelector('#calcBody').innerHTML = `
    <div class="panel no-print"><div class="small muted">A one-page sheet for ${esc(d.who.name)}: where the year stands, and what a pay target needs, with nothing about any other clinician. Print it or save it as a PDF.</div>
      <div class="filters" style="margin-top:6px"><label class="small">Pay target, a year <input id="sheetTarget" inputmode="numeric" placeholder="e.g. 650,000" value="${esc(target)}" style="width:140px"></label>
      <button id="sheetPrint">Print</button></div>
      ${plan ? '' : `<div class="small sev-medium">No pay plan entered for ${esc(d.who.name)} under Practice figures: the sheet shows collections only.</div>`}</div>
    <div class="panel sheet" id="sheetBody"></div>`;
  const body = v.querySelector('#calcBody');
  const draw = () => {
    const t = parseFloat(String(cs.sheetTarget || '').replace(/[$,\s]/g, '').replace(/k$/i, '000')) * 100;
    const need = plan && isFinite(t) && t > 0 ? (plan.type === 'wrvu' ? null : LabModel.collectionsFor(plan, t)) : null;
    const needW = plan && plan.type === 'wrvu' && isFinite(t) && t > 0 && plan.per_wrvu ? t / 100 / plan.per_wrvu : null;
    const days = planned.daysYear || 1, dist = d.schedule.clinic_day_allowed || {};
    let goal = '';
    if(need){
      const res = CalcModel.goalSeek(d, x, need, cs.cal);
      const gap = res.length ? res[0].gap : 0, dpw = Math.max(0.5, x.days_per_week);
      goal = `<h3>To be paid ${moneyK(t)}</h3><p>Collections of ${moneyK(need)} a year for your own production: ${money(need / days)} a clinic day over ${days.toFixed(0)} days.
        ${dist.p50 ? `Your median clinic day has brought ${money(dist.p50)} allowed, your best tenth ${money(dist.p90)}.` : ''}</p>
        ${gap > 0 ? `<p>${moneyK(gap)} more than this plan. Any one of these closes it:</p><table><tbody>${res.filter(m => m.needed != null && !['days_per_week', 'weeks_per_year'].includes(m.key)).map(m =>
          `<tr><td>${esc(m.name)}</td><td class="num">${(m.needed / dpw).toFixed(1)} more a clinic day</td></tr>`).join('')}</tbody></table>` : `<p class="ok">This plan reaches it.</p>`}`;
    } else if(needW){
      goal = `<h3>To be paid ${moneyK(t)}</h3><p>${Math.round(needW).toLocaleString()} work RVUs a year: ${(needW / days).toFixed(1)} a clinic day over ${days.toFixed(0)} days. This plan holds ${Math.round(wPlan).toLocaleString()}.</p>`;
    }
    body.querySelector('#sheetBody').innerHTML = `
      <h2>${esc(d.who.name)}: ${esc(x.year)}</h2>
      <table><thead><tr><th></th><th class="num">At today's pace</th><th class="num">The plan</th></tr></thead><tbody>
        <tr><td>Clinic days a year</td><td class="num">${(now.daysYear||0).toFixed(0)}</td><td class="num">${(planned.daysYear||0).toFixed(0)}</td></tr>
        <tr><td>Mohs cases a clinic day</td><td class="num">${calcFmt.n1(cs.base.mohs_cases)}</td><td class="num">${calcFmt.n1(x.mohs_cases)}</td></tr>
        <tr><td>Visits a clinic day</td><td class="num">${calcFmt.n1(cs.base.visits)}</td><td class="num">${calcFmt.n1(x.visits)}</td></tr>
        <tr><td>Patients biopsied a clinic day</td><td class="num">${calcFmt.n1(cs.base.bx_patients)}</td><td class="num">${calcFmt.n1(x.bx_patients)}</td></tr>
        <tr><td>Excisions a clinic day</td><td class="num">${calcFmt.n1(cs.base.excisions)}</td><td class="num">${calcFmt.n1(x.excisions)}</td></tr>
        <tr><td><b>Collections for your own production</b></td><td class="num"><b>${moneyK(now.collections)}</b></td><td class="num"><b>${moneyK(planned.collections)}</b></td></tr>
        <tr><td>Work RVUs</td><td class="num">${Math.round(wNow).toLocaleString()}</td><td class="num">${Math.round(wPlan).toLocaleString()}</td></tr>
        ${plan ? `<tr><td>Pay under your plan</td><td class="num">${labMoney(LabModel.pay(plan, now.collections, wNow))}</td><td class="num">${labMoney(LabModel.pay(plan, planned.collections, wPlan))}</td></tr>` : ''}
      </tbody></table>
      ${goal}
      <p class="small muted">Collections are estimates from the practice's remittances at each payer's rates${x.year === d.next_year ? ` and Medicare's ${esc(x.year)} rules` : ''}, for services you perform yourself. Pathology, radiation, drugs and other services you order are not counted, as the law requires for productivity pay. Stages, closures and visit levels follow each patient's needs and are not targets.</p>`;
  };
  body.querySelector('#sheetTarget').oninput = e => { cs.sheetTarget = e.target.value; draw(); };
  body.querySelector('#sheetPrint').onclick = () => window.print();
  draw();
}
