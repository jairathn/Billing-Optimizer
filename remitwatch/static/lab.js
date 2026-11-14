/* ==================================================================== Strategy lab, practice figures, provider sheet
   Three more views of the Calculator tab, for the owner. The lab starts from the calculator's plan and stacks
   strategies (lab_model.js); the practice figures (minutes, costs, pay plans) are saved to the site
   (/api/activity/assumptions); the provider sheet turns a pay target into what a clinic day has to hold, for one
   clinician, printable.

   Saving: the figures are read once, with a version. A save sends one change (these figures, this scenario) applied to
   the copy read, with its version; when another window saved in between, the server refuses it (409) and the change
   is applied again to a fresh read, once. A read that fails leaves saving off, so blanks are never written over what is
   saved. */

const LAB_GROUPS = [
  ['Money already earned', ['appeals', 'vendor_fees', 'collect']],
  ['Coding and contracts', ['b1', 'contract_month']],
  ['Staffing and clinic time', ['app_shift', 'add_session']],
  ['Where cases are done, and by whom', ['asc', 'other_surgeon']],
  ['Timing of procedures: what-ifs, never in the plan', ['stage', 'biopsy_return']],
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
  if(cs.A && cs.Aok && !force) return cs.A;
  try {
    const r = await api('/api/activity/assumptions');
    if(r.error) throw new Error(r.error);
    const saved = r.assumptions;
    cs.Adoc = LabModel.clean(saved); cs.Aver = r.version || null;
    cs.Asaved = saved && typeof saved.saved_at === 'string' ? saved.saved_at : null;
    cs.A = LabModel.filled(cs.Adoc); cs.Aok = true; cs.Aerror = null;
  } catch(e){
    // keep what was read before (or blanks, to work with), but saving stays off until a read succeeds
    cs.Aok = false; cs.Aerror = e.message || 'The server did not answer.';
    if(!cs.A) cs.A = LabModel.filled(null);
  }
  cs.lab = cs.lab || {};
  return cs.A;
}

function labReadProblem(){
  return `The saved figures could not be read (${state.calc.Aerror || 'no answer'}). Saving is off, so nothing saved is overwritten. Open this view again to try again.`;
}
function labReadNote(){
  return state.calc.Aok ? '' : `<div class="note sev-high" style="margin-top:8px">${esc(labReadProblem())}</div>`;
}

// change(doc) makes one change to a copy of the saved figures; returns {rebased} (true when another window had saved)
async function labSave(change){
  const cs = state.calc;
  for(let attempt = 0; ; attempt++){
    if(!cs.Aok) throw new Error(labReadProblem());
    const doc = LabModel.clean(cs.Adoc);
    change(doc);
    try {
      const r = await api('/api/activity/assumptions', {method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-Figures-Version': cs.Aver || ''}, body: JSON.stringify(doc)});
      cs.Adoc = doc; cs.Aver = r.version || null; cs.Asaved = r.saved_at || null; cs.A = LabModel.filled(doc);
      (cs.lab || {}).scenCache = null;
      return {rebased: attempt > 0};
    } catch(e){
      if(e.status !== 409 || attempt > 0) throw e;
      await labAssumptions(true);
    }
  }
}

const labMoney = c => c == null ? '–' : moneyK(c);
const labSigned = c => !c || Math.abs(c) < 50000 ? '<span class="muted">–</span>' : `<span class="${c>0?'ok':'sev-high'}">${c>0?'+':'−'}${moneyK(Math.abs(c))}</span>`;
const LAB_NEED_LABEL = Object.fromEntries([...LabModel.MINUTES.map(([k, l]) => ['minutes.' + k, 'minutes: ' + l.toLowerCase()]),
                                           ...LabModel.COSTS.map(([k, l]) => ['costs.' + k, 'cost: ' + l.toLowerCase()])]);
const labNum = v => (typeof v === 'number' && isFinite(v)) ? v : null;

// ------------------------------------------------------------------ the lab
async function labDraw(v){
  const cs = state.calc, d = cs.data, x = cs.x;
  await labAssumptions();
  const lab = cs.lab = cs.lab || {};
  lab.chosen = lab.chosen || {};
  v.querySelector('#calcBody').innerHTML = `
    <div class="panel"><h2>Strategy lab</h2>
      <div class="small muted">Starts from the plan on the Calculator view (${esc(d.who.name)}, ${esc(x.year)}${x.year===d.next_year?' Medicare rules':' rates'}). Tick strategies to stack them; each line shows what it adds on top of the ones above it. Minutes and costs come from Practice figures; where one is missing the line says so instead of guessing. The timing what-ifs are shown apart and never added to the plan.</div>
      ${labReadNote()}
      <div id="labSummary" style="margin-top:8px"></div></div>
    ${LAB_GROUPS.map(([title, ids]) => `<div class="panel"><h2>${esc(title)}</h2>${ids.map(id => labCard(LabModel.STRATEGIES.find(s => s.id === id))).join('')}</div>`).join('')}
    <div class="panel" id="labMinutes"></div>
    <div class="panel" id="labScenarios"></div>`;
  const body = v.querySelector('#calcBody');
  body.querySelectorAll('[data-lab-on]').forEach(cb => cb.onchange = () => { const id = cb.dataset.labOn; lab.chosen[id] = Object.assign(lab.chosen[id] || {}, {on: cb.checked}); labUpdate(v); });
  body.querySelectorAll('[data-lab-p]').forEach(inp => {
    const [id, key, fmt] = inp.dataset.labP.split('|');
    const S = LabModel.STRATEGIES.find(s => s.id === id), def = S && S.params.find(q => q[0] === key);
    const set = e => {
      let val = inp.type === 'checkbox' ? (inp.checked ? 1 : 0) : parseFloat(inp.value);
      if(!isFinite(val)) return;
      if(fmt === 'pct' && inp.type !== 'range') val = val / 100;
      if(def) val = Math.min(def[3], Math.max(def[2], val));
      // when typing ends, the box shows the value used
      if(e && e.type === 'change' && inp.type === 'number') inp.value = fmt === 'pct' ? Math.round(val * 1000) / 10 : val;
      const c = lab.chosen[id] = lab.chosen[id] || {on: false};
      c.params = Object.assign(c.params || {}, {[key]: val});
      const twin = body.querySelector(`[data-lab-twin="${id}|${key}"]`);
      if(twin && inp.type === 'range') twin.value = fmt === 'pct' ? Math.round(val * 1000) / 10 : val;
      if(twin && inp.type !== 'range'){ const r = body.querySelector(`input[type=range][data-lab-p="${id}|${key}|${fmt}"]`); if(r) r.value = val; }
      labSoon(v);
    };
    inp.oninput = set; inp.onchange = set;
  });
  labScenariosInit(v);
  labUpdate(v);
}

function labCard(S){
  const cs = state.calc, d = cs.data, c = (cs.lab.chosen || {})[S.id] || {}, p = Object.assign(LabModel.paramDefaults(S), c.params || {});
  const [tier, cls] = LAB_TIER[S.tier] || ['', ''];
  const off = (S.only === 'next' && cs.x.year !== d.next_year) || (S.applies && !S.applies(d, cs.x));
  const ctl = ([key, label, mn, mx, st, fmt]) => {
    const val = labNum(p[key]);
    if(fmt === 'bool') return `<label class="small" style="display:block"><input type="checkbox" data-lab-p="${esc(S.id)}|${esc(key)}|bool" ${val?'checked':''}> ${esc(label)}</label>`;
    const shown = val == null ? '' : fmt === 'pct' ? Math.round(val * 1000) / 10 : val;
    const k = fmt === 'pct' ? 100 : 1;
    return `<div class="calc-row"><label>${esc(label)}</label><div class="calc-ctl">
      <input type="range" min="${mn}" max="${mx}" step="${st}" value="${val == null ? mn : val}" data-lab-p="${esc(S.id)}|${esc(key)}|${esc(fmt)}" aria-label="${esc(label)}">
      <input type="number" class="calc-num" min="${mn * k}" max="${mx * k}" step="any" value="${esc(shown)}" placeholder="${val == null ? 'not entered' : ''}" data-lab-p="${esc(S.id)}|${esc(key)}|${esc(fmt)}" data-lab-twin="${esc(S.id)}|${esc(key)}" aria-label="${esc(label)}">${fmt==='pct'?'<span class="small">%</span>':''}</div></div>`;
  };
  return `<div class="lab-card" data-lab-card="${esc(S.id)}">
    <label class="lab-head"><input type="checkbox" data-lab-on="${esc(S.id)}" ${c.on?'checked':''} ${off?'disabled':''}> <b>${esc(S.title)}</b>
      <span class="chip ${cls}">${esc(tier)}</span>${S.whatif ? ' <span class="chip">what-if</span>' : ''}</label>
    <div class="small muted">${esc(S.why)}${off ? (S.applies && !S.applies(d, cs.x) ? ' Not for this clinician\'s work.' : ' Switch the Calculator to Plan ' + esc(d.next_year) + ' to use it.') : ''}</div>
    <div class="lab-impact small" data-lab-impact="${esc(S.id)}"></div>
    <details><summary class="small">Settings${S.compliance ? ' and compliance' : ''}</summary>
      ${S.params.map(ctl).join('')}
      ${S.compliance ? `<div class="small ${S.tier==='high'?'sev-high':''}" style="margin-top:6px">${esc(S.compliance)}</div>` : ''}
    </details></div>`;
}

let labTimer = null;
function labSoon(v){ if(labTimer) return; labTimer = requestAnimationFrame(() => { labTimer = null; labUpdate(v); }); }

function labNeeds(S, needs){
  const fig = needs.filter(n => !n.startsWith('param.')), par = needs.filter(n => n.startsWith('param.'));
  const parLabel = n => { const q = (S.params || []).find(p => p[0] === n.slice(6)); return (q ? q[1].toLowerCase() : n) + ' (in this card\'s settings)'; };
  return `<div class="sev-medium">Not entered yet: ${[...fig.map(n => esc(LAB_NEED_LABEL[n] || n)), ...par.map(n => esc(parLabel(n)))].join('; ')}.${fig.length ? ' <a href="#" data-goto-figures>Practice figures</a>' : ''}</div>`;
}

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
    const bits = [`${st.whatif ? 'what-if: ' : ''}collections ${labSigned(st.collections)} a year`];
    if(Math.abs(st.cost) >= 50000) bits.push(`costs ${st.cost > 0 ? '+' : '−'}${moneyK(Math.abs(st.cost))}`);
    if(Math.abs(st.cost) >= 50000) bits.push(`net ${labSigned(st.collections - st.cost)}`);
    if(Math.abs(st.encountersDay) >= 0.01) bits.push(`${st.encountersDay > 0 ? '+' : ''}${st.encountersDay.toFixed(2)} visits a clinic day`);
    if(Math.abs(st.minutesDay) >= 0.5) bits.push(`${st.minutesDay > 0 ? '+' : ''}${st.minutesDay.toFixed(0)} clinician minutes a day`);
    if(st.oneTime) bits.push(`one-time ${moneyK(st.oneTime)}`);
    el.innerHTML = bits.join(' · ') + (st.notes.length ? `<div class="muted">${st.notes.map(esc).join(' ')}</div>` : '')
      + (st.needs.length ? labNeeds(S, st.needs) : '');
  }
  const R = r.result, B = r.base, W = r.whatif;
  const costKnown = !R.costs.missing.length;
  const comp = labPayFor(d, R);
  const plan = (cs.A.comp || {})[d.who.id];
  const planSteps = r.steps.filter(s => !s.skipped && !s.whatif), ifSteps = r.steps.filter(s => !s.skipped && s.whatif);
  body.querySelector('#labSummary').innerHTML = `<table class="calc-fall"><tbody>
    <tr><td>The calculator's plan</td><td class="num">${moneyK(B.collections)}</td></tr>
    ${planSteps.map(s => `<tr><td>${esc(s.title)}</td><td class="num">${labSigned(s.collections)}</td></tr>`).join('')}
    <tr class="calc-total"><td><b>Collections a year with these strategies</b>${R.practiceOnly >= 50000 ? `<div class="small muted">of which ${moneyK(R.practiceOnly)} is the practice's (ancillary lines, APP visits, vendor fees, another surgeon's closures), not this clinician's production</div>` : ''}</td><td class="num"><b style="font-size:20px">${moneyK(R.collections)}</b></td></tr>
    <tr><td>Costs a year${costKnown ? '' : ' <span class="small sev-medium">(some not entered)</span>'}</td><td class="num">${R.cost ? moneyK(R.cost) : '<span class="muted">not entered</span>'}</td></tr>
    ${R.cost ? `<tr><td><b>Left after costs</b></td><td class="num"><b>${moneyK(R.collections - R.cost)}</b> <span class="small muted">(${labSigned((R.collections - R.cost) - (B.collections - B.cost))})</span></td></tr>` : ''}
    ${R.oneTime ? `<tr><td>One-time cash (appeals)</td><td class="num">${moneyK(R.oneTime)}</td></tr>` : ''}
    <tr><td>Clinician minutes a clinic day${r.capacity ? '' : ' <span class="small muted">(enter the minutes in Practice figures)</span>'}</td>
      <td class="num">${R.minutesDay ? R.minutesDay.toFixed(0) : '–'}${r.capacity ? ` of ${r.capacity.available} <span class="${r.capacity.share > 1 ? 'sev-high' : 'muted'}">(${Math.round(r.capacity.share * 100)}%)</span>` : ''}</td></tr>
    <tr><td>Work RVUs a year</td><td class="num">${Math.round(R.wrvu).toLocaleString()}${Math.abs(R.wrvu - B.wrvu) >= 1 ? ` <span class="small muted">(${R.wrvu > B.wrvu ? '+' : ''}${Math.round(R.wrvu - B.wrvu).toLocaleString()})</span>` : ''}</td></tr>
    ${comp ? `<tr><td>Pay under ${esc(comp.label)}<div class="small muted">on own production of ${moneyK(R.ownProduction)}</div></td><td class="num">${moneyK(comp.pay)}</td></tr>`
      : plan && plan.type ? `<tr><td>Pay</td><td class="num small sev-medium">enter ${esc(LabModel.planMissing(plan) || 'the plan')} in Practice figures</td></tr>` : ''}
    ${ifSteps.length ? `<tr><td colspan="2" class="small muted" style="padding-top:10px">What-ifs, not in the plan above:</td></tr>
      ${ifSteps.map(s => `<tr><td>${esc(s.title)}</td><td class="num">${labSigned(s.collections)}</td></tr>`).join('')}
      ${W ? `<tr><td>Collections a year with the what-ifs</td><td class="num">${moneyK(W.collections)}${W.cost ? ` <span class="small muted">(after costs ${moneyK(W.collections - W.cost)})</span>` : ''}</td></tr>` : ''}` : ''}
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
  labScenarioRows(v);
}

// pay is worked out on the clinician's own production only, whatever the calculator adds to the totals
function labPayFor(d, R){
  const cs = state.calc, plan = (cs.A.comp || {})[d.who.id];
  if(!plan || !plan.type) return null;
  const p = LabModel.pay(plan, R.ownProduction, R.wrvu);
  if(p == null) return null;
  return {pay: p, label: (LabModel.COMP_TYPES.find(t => t[0] === plan.type) || ['', 'the pay plan'])[1].toLowerCase()};
}

// ------------------------------------------------------------------ scenarios: saved with the practice figures
// a saved plan, taken back only where each setting has the kind of value the calculator's own has
function labPlanFrom(d, sx){
  const base = CalcModel.defaults(d), out = JSON.parse(JSON.stringify(base));
  const has = (o, k) => o && Object.prototype.hasOwnProperty.call(o, k);
  const years = [d.rate_year, d.next_year, ...(d.years || [])].filter(Boolean).map(String);
  for(const [k, def] of Object.entries(base)){
    if(!has(sx, k)) continue;
    const v = sx[k];
    if(k === 'year'){ if(years.includes(String(v))) out.year = typeof def === 'string' ? String(v) : v; continue; }
    if(typeof def === 'number'){ if(labNum(v) != null) out[k] = v; }
    else if(typeof def === 'boolean'){ if(typeof v === 'boolean') out[k] = v; }
    else if(typeof def === 'string'){ if(typeof v === 'string' && /^[a-z_]{1,30}$/.test(v)) out[k] = v; }
    else if(def && typeof def === 'object' && !Array.isArray(def) && v && typeof v === 'object' && !Array.isArray(v)){
      const known = Object.keys(def).length ? Object.keys(def) : Object.keys(d.payers || {});
      for(const kk of known){
        if(!has(v, kk)) continue;
        const w = v[kk], t = has(def, kk) ? typeof def[kk] : null;
        if(t ? (typeof w === t && (t !== 'number' || labNum(w) != null)) : (labNum(w) != null || typeof w === 'boolean')) out[k][kk] = w;
      }
    }
  }
  return out;
}

function labScenariosInit(v){
  const cs = state.calc, box = v.querySelector('#labScenarios');
  box.innerHTML = `<h2>Scenarios</h2>
    <div class="small muted">Save the calculator's plan and the ticked strategies under a name, then compare. Saved with the practice figures, for the owner only. The what-ifs are kept with a scenario but never counted in its figures.</div>
    <div class="filters" style="margin:6px 0"><input id="labName" placeholder="Name this scenario" maxlength="100" style="min-width:220px" aria-label="scenario name">
      <button id="labSaveScen" ${cs.Aok ? '' : 'disabled'}>Save</button> <span class="small" id="labSaved" role="status"></span></div>
    <div id="labScenTable"></div>`;
  const say = (text, cls) => { const m = box.querySelector('#labSaved'); m.textContent = text; m.className = 'small ' + (cls || ''); };
  if(!cs.Aok) say(labReadProblem(), 'sev-high');
  box.querySelector('#labSaveScen').onclick = async () => {
    const d = cs.data, inp = box.querySelector('#labName'), name = inp.value.trim().slice(0, 100);
    if(!name){ say('Give the scenario a name first.', 'sev-medium'); inp.focus(); return; }
    const all = (cs.Adoc || {}).scenarios || [];
    if(all.length >= LabModel.MAX_SCENARIOS && !all.some(s => s.who === d.who.id && s.name === name)){
      say(`There are already ${LabModel.MAX_SCENARIOS} saved scenarios: delete some first.`, 'sev-high'); return; }
    const item = {name, who: d.who.id, x: JSON.parse(JSON.stringify(cs.x)), chosen: JSON.parse(JSON.stringify(cs.lab.chosen || {})), saved_at: new Date().toISOString()};
    say('Saving…', 'muted');
    try {
      const r = await labSave(doc => { doc.scenarios = doc.scenarios.filter(s => !(s.who === item.who && s.name === item.name)); doc.scenarios.push(item); });
      inp.value = '';
      say(r.rebased ? `Saved "${name}". Figures saved in another window meanwhile were kept.` : `Saved "${name}".`, 'ok');
    } catch(e){ say(e.message, 'sev-high'); }
    labScenarioRows(v);
  };
}

function labScenarioRows(v){
  const cs = state.calc, d = cs.data, A = cs.A, box = v.querySelector('#labScenTable');
  if(!box) return;
  const mine = (A.scenarios || []).filter(s => s.who === d.who.id);
  const cache = cs.lab.scenCache = cs.lab.scenCache || {};
  const rows = mine.map(s => {
    const key = JSON.stringify([d._for, cs.Aver, s.name, s.saved_at]);
    if(!cache[key]) cache[key] = LabModel.run(d, labPlanFrom(d, s.x), A, s.chosen || {}, cs.cal);
    return {s, r: cache[key]};
  });
  const cur = cs.lab.last;
  const cells = R => `<td class="num" data-label="Collections">${moneyK(R.collections)}</td><td class="num" data-label="Costs">${R.cost ? moneyK(R.cost) : '–'}</td><td class="num" data-label="After costs">${R.cost ? moneyK(R.collections - R.cost) : '–'}</td><td class="num" data-label="Minutes a day">${R.minutesDay ? R.minutesDay.toFixed(0) : '–'}</td>`;
  box.innerHTML = rows.length ? `<table class="calc-cards"><thead><tr><th>Scenario</th><th class="num">Collections</th><th class="num">Costs</th><th class="num">After costs</th><th class="num">Minutes a day</th><th></th></tr></thead><tbody>
      ${cur ? `<tr><td><i>Now on screen</i></td>${cells(cur.result)}</tr>` : ''}
      ${rows.map(({s, r}, i) => `<tr><td>${esc(s.name)}<div class="small muted">${esc((s.saved_at || '').slice(0, 10))} · ${esc((s.x || {}).year || '')}</div></td>${cells(r.result)}
        <td class="num" style="grid-column:1 / -1"><button data-lab-load="${i}">Open</button> <button class="ghost" data-lab-del="${i}" ${cs.Aok ? '' : 'disabled'}>Delete</button></td></tr>`).join('')}
      </tbody></table>` : '';
  const say = (text, cls) => { const m = v.querySelector('#labSaved'); if(m){ m.textContent = text; m.className = 'small ' + (cls || ''); } };
  box.querySelectorAll('[data-lab-load]').forEach(b => b.onclick = () => { const s = mine[+b.dataset.labLoad];
    cs.x = labPlanFrom(d, s.x); cs.lab.chosen = JSON.parse(JSON.stringify(s.chosen || {})); calcDraw(cs.el); });
  box.querySelectorAll('[data-lab-del]').forEach(b => b.onclick = async () => { const s = mine[+b.dataset.labDel];
    b.disabled = true;
    try {
      await labSave(doc => { doc.scenarios = doc.scenarios.filter(t => !(t.who === s.who && t.name === s.name && t.saved_at === s.saved_at)); });
      say(`Deleted "${s.name}".`, 'ok');
    } catch(e){ say('Not deleted: ' + e.message, 'sev-high'); }
    labScenarioRows(v);
  });
}

// ------------------------------------------------------------------ practice figures
async function figuresDraw(v, note){
  const cs = state.calc, d = cs.data;
  const A = await labAssumptions();
  const show = val => labNum(val) == null ? '' : String(val);
  const pctShow = val => labNum(val) == null ? '' : String(Math.round(val * 1000) / 10);
  const field = (group, key, label, unit) => { const val = show(group === 'session' ? A.session_minutes : A[group][key]);
    return `<div class="calc-row"><label for="f_${group}_${key}">${esc(label)}</label>
    <div class="calc-ctl"><input type="number" id="f_${group}_${key}" data-fig="${group}.${key}" data-label="${esc(label)}" data-orig="${esc(val)}" min="0" step="any" placeholder="not entered" value="${esc(val)}" style="width:120px" ${cs.Aok ? '' : 'disabled'}> <span class="small muted">${esc(unit)}</span></div></div>`; };
  const people = (d.clinicians || []).map(c => [c.provider_id, c.name]).concat(Object.entries(ROLE_LABEL).map(([k, l]) => [k, l + ' (average)']));
  const comp = A.comp || {};
  const compRow = ([id, name]) => { const p = comp[id] || {};
    const inp = (k, val, ph, w, title, label) => `<td data-label="${esc(label)}"><input type="number" data-c="${k}" data-orig="${esc(val)}" min="0" step="any" placeholder="${esc(ph)}" value="${esc(val)}" style="width:${w}px"${title ? ` title="${esc(title)}"` : ''} aria-label="${esc(name)}: ${esc(ph)}" ${cs.Aok ? '' : 'disabled'}></td>`;
    return `<tr data-comp="${esc(id)}" data-name="${esc(name)}"><td>${esc(name)}</td><td data-label="Plan" style="grid-column:1 / -1"><select data-c="type" data-orig="${esc(p.type || '')}" aria-label="${esc(name)}: plan" ${cs.Aok ? '' : 'disabled'}><option value="">no plan entered</option>${LabModel.COMP_TYPES.map(([k, l]) => `<option value="${k}" ${p.type===k?'selected':''}>${esc(l)}</option>`).join('')}</select></td>
      ${inp('pct', pctShow(p.pct), 'share, %', 90, 'share of collections, %', 'Share of collections')}${inp('per_wrvu', show(p.per_wrvu), '$ per wRVU', 90, '', 'Per wRVU')}${inp('base', show(p.base), 'base $', 100, '', 'Base')}${inp('threshold', show(p.threshold), 'threshold $', 110, '', 'Threshold')}${inp('above_pct', pctShow(p.above_pct), 'above, %', 80, '', 'Share above')}</tr>`; };
  v.querySelector('#calcBody').innerHTML = `
    <div class="panel"><h2>Practice figures</h2>
      <div class="small muted">The practice's own numbers, used by the Strategy lab and the Provider sheet. Leave a box empty until you have the figure: anything that depends on it is shown as not entered, never guessed. Saved to the site for the owner only${cs.Asaved ? `; last saved ${esc(cs.Asaved.replace('T', ' ').slice(0, 16))}` : ''}. Only the boxes you change are saved, so figures saved in another window are kept.</div>
      ${labReadNote()}
      <div class="actions" style="margin-top:8px"><button class="primary" id="figSave" ${cs.Aok ? '' : 'disabled'}>Save</button> ${cs.Aok ? '' : '<button id="figRetry">Read them again</button>'} <span id="figMsg" class="small" role="status"></span></div></div>
    <div class="grid two">
      <div class="panel"><h2>Clinician minutes</h2><div class="small muted">Hands-on minutes of the clinician for each piece of work, not room time. A Mohs surgeon running several rooms counts only the minutes spent with each patient and at the microscope.</div>
        ${field('session', 'minutes', 'Minutes the clinician has in a clinic day', 'minutes')}
        ${LabModel.MINUTES.map(([k, l]) => field('minutes', k, l, 'minutes')).join('')}</div>
      <div class="panel"><h2>Costs</h2><div class="small muted">Dollars. A clinic day's fixed cost is what runs whether patients come or not; the rest are per piece of work.</div>
        ${LabModel.COSTS.map(([k, l]) => field('costs', k, l, 'dollars')).join('')}</div>
    </div>
    <div class="panel"><h2>Pay plans</h2><div class="small muted">How each clinician is paid on their own production: services they perform personally (slides they read themselves included) and services given incident to their own. Pathology, lab, radiation and drugs they order are designated health services: under the Stark law a physician's productivity pay may not credit them. An APP's pay is kept to their own services under the anti-kickback safe harbor for employees. Have counsel review each plan. Plans of clinicians not listed here are kept.</div>
      <div><table class="calc-cards"><thead><tr><th>Clinician</th><th>Plan</th><th>Share of collections</th><th>Per wRVU</th><th>Base</th><th>Threshold</th><th>Share above</th></tr></thead><tbody>${people.map(compRow).join('')}</tbody></table></div></div>`;
  const body = v.querySelector('#calcBody'), msg = body.querySelector('#figMsg');
  const say = (text, cls) => { msg.textContent = text; msg.className = 'small ' + (cls || ''); };
  if(note) say(note[0], note[1]);
  const retry = body.querySelector('#figRetry');
  if(retry) retry.onclick = async () => { await labAssumptions(true); figuresDraw(v); };
  body.querySelector('#figSave').onclick = async () => {
    const bad = [], figs = [], plans = [];
    const read = (inp, maxPct) => {      // null for an empty box, undefined for a value that is not allowed
      const s = String(inp.value).trim();
      if(s === '') return inp.validity && inp.validity.badInput ? undefined : null;
      const n = Number(s);
      return isFinite(n) && n >= 0 && n <= 1e9 && (!maxPct || n <= 100) ? n : undefined;
    };
    body.querySelectorAll('[data-fig]').forEach(inp => {
      if(inp.value === inp.dataset.orig && !(inp.validity && inp.validity.badInput)) return;
      const n = read(inp);
      if(n === undefined) bad.push(inp.dataset.label); else figs.push([inp.dataset.fig, n]);
    });
    body.querySelectorAll('tr[data-comp]').forEach(tr => {
      const els = [...tr.querySelectorAll('[data-c]')];
      if(els.every(i => i.value === i.dataset.orig && !(i.validity && i.validity.badInput))) return;
      const type = tr.querySelector('[data-c="type"]').value;
      const val = (k, pct) => { const n = read(tr.querySelector(`[data-c="${k}"]`), pct); if(n === undefined) bad.push(tr.dataset.name + ': ' + k.replace('_', ' ')); return n == null ? null : (pct ? n / 100 : n); };
      const plan = {type, pct: val('pct', true), per_wrvu: val('per_wrvu'), base: val('base'), threshold: val('threshold'), above_pct: val('above_pct', true)};
      plans.push([tr.dataset.comp, type ? plan : null]);
    });
    if(bad.length){ say('Each figure must be a number of 0 or more (shares at most 100%). Check: ' + bad.join('; ') + '.', 'sev-high'); return; }
    if(!figs.length && !plans.length){ say('Nothing has changed.', 'muted'); return; }
    say('Saving…', 'muted');
    try {
      const r = await labSave(doc => {
        for(const [path, n] of figs){ const [g, k] = path.split('.'); if(g === 'session') doc.session_minutes = n; else doc[g][k] = n; }
        for(const [id, plan] of plans){ if(plan) doc.comp[id] = plan; else delete doc.comp[id]; }
      });
      await figuresDraw(v, [r.rebased ? 'Saved. Figures saved in another window meanwhile were kept.' : 'Saved.', 'ok']);
    } catch(e){ say(e.message, 'sev-high'); }
  };
}

// ------------------------------------------------------------------ provider sheet
// a pay target as typed: 650000, $650,000, 650k, 0.65m
function sheetAmount(s){
  if(typeof calcMoney === 'function'){ const v = calcMoney(s); return isFinite(v) ? v * 100 : NaN; }    // the calculator's own reading
  const m = String(s || '').replace(/[$,\s]/g, '').match(/^(\d+(?:\.\d+)?|\.\d+)([km])?$/i);
  if(!m) return NaN;
  return parseFloat(m[1]) * ({k: 1e3, m: 1e6}[(m[2] || '').toLowerCase()] || 1) * 100;
}

async function sheetDraw(v){
  const cs = state.calc, d = cs.data, x = cs.x;
  if(ROLE_LABEL[d.who.id]){
    v.querySelector('#calcBody').innerHTML = `<div class="panel"><h2>Provider sheet</h2><div class="note">The provider sheet is for one clinician: it shows their own figures and nothing about anyone else. An average pools several clinicians: use Pick another clinician above to choose one, then open this view again.</div></div>`;
    return;
  }
  const A = await labAssumptions();
  const plan = (A.comp || {})[d.who.id] || null;
  // the clinician's own production only, whatever the calculator adds to its totals
  const own = y => Object.assign({}, y, {include_ancillary: false, include_path: false});
  const xp = own(x);
  const now = CalcModel.evaluate(d, own(calcWith(cs.base, x, {year: x.year})), cs.cal);
  const planned = CalcModel.evaluate(d, xp, cs.cal);
  const wNow = LabModel.wrvus(d, calcWith(cs.base, x, {year: x.year})), wPlan = LabModel.wrvus(d, x);
  const target = cs.sheetTarget || '';
  const app = d.tier === 'nonphysician';
  v.querySelector('#calcBody').innerHTML = `
    <div class="panel no-print"><div class="small muted">A one-page sheet for ${esc(d.who.name)}: where the year stands, and what a pay target needs, with nothing about any other clinician. Print it or save it as a PDF.</div>
      <div class="filters" style="margin-top:6px"><label class="small">Pay target, a year <input id="sheetTarget" inputmode="decimal" placeholder="e.g. 650,000 or 650k" value="${esc(target)}" style="width:140px"></label>
      <button id="sheetPrint">Print</button></div>
      ${plan ? '' : `<div class="small sev-medium">No pay plan entered for ${esc(d.who.name)} under Practice figures: the sheet shows collections only.</div>`}
      <div id="sheetNote" class="small sev-medium"></div></div>
    <div class="panel sheet" id="sheetBody"></div>`;
  const body = v.querySelector('#calcBody');
  const draw = () => {
    const t = sheetAmount(cs.sheetTarget);
    const has = plan && isFinite(t) && t > 0;
    const missing = has ? LabModel.planMissing(plan, t) : null;
    const need = has && !missing && plan.type !== 'wrvu' ? LabModel.collectionsFor(plan, t) : null;
    const needW = has && !missing && plan.type === 'wrvu' ? t / 100 / plan.per_wrvu : null;
    const days = planned.daysYear || 1, dist = d.schedule.clinic_day_allowed || {};
    body.querySelector('#sheetNote').textContent = String(cs.sheetTarget || '').trim() && !(isFinite(t) && t > 0) ? 'Type the target as a number of dollars, such as 650000 or 650k.'
      : missing ? `Enter ${missing} for ${d.who.name}'s plan under Practice figures to see what the target needs.` : '';
    let goal = '';
    if(need === 0){
      goal = `<h3>To be paid ${moneyK(t)}</h3><p>Your base pay covers this target.</p>`;
    } else if(need){
      const dpw = Math.max(0.5, x.days_per_week);
      // like with like: the collections a day needs, as allowed amounts at this plan's collected share
      const ratio = planned.allowed > 0 ? planned.collections / planned.allowed : null;
      const ids = ['visits', 'new_visits', 'days_per_week', 'weeks_per_year', 'mohs_cases'].filter(k => CalcModel.MOVES.some(m => m[0] === k));
      const seek = CalcModel.goalSeek(d, xp, need, cs.cal, {moves: ids, production: true});
      const gap = seek.gap != null ? seek.gap : need - planned.collections;      // on own production
      const res = gap > 0 ? seek.filter(m => ids.includes(m.key) && m.needed != null && isFinite(m.needed) && m.needed > 0) : [];
      const how = m => m.key === 'days_per_week' ? `${(m.needed * m.step).toFixed(1)} more clinic days a week`
        : m.key === 'weeks_per_year' ? `${(m.needed * m.step).toFixed(1)} more weeks of clinic a year`
        : `${(m.needed / dpw).toFixed(1)} more a clinic day${m.key === 'mohs_cases' ? ' (depends on referrals)' : ''}`;
      goal = `<h3>To be paid ${moneyK(t)}</h3><p>Collections of ${moneyK(need)} a year for your own production: ${money(need / days)} a clinic day over ${days.toFixed(0)} days${ratio ? `, about ${money(need / days / ratio)} allowed` : ''}.
        ${dist.p50 && ratio ? `Your median clinic day has brought ${money(dist.p50)} allowed, your best tenth ${money(dist.p90)}.` : ''}</p>
        ${gap > 0 ? `<p>${moneyK(gap)} more than this plan.${res.length ? ' Any one of these closes it:' : ' None of the patient-volume levers moves this plan\'s collections.'}</p>${res.length ? `<table><tbody>${res.map(m =>
          `<tr><td>${esc(m.name)}</td><td class="num">${how(m)}</td></tr>`).join('')}</tbody></table>` : ''}` : `<p class="ok">This plan reaches it.</p>`}`;
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
      <p class="small muted">Collections are estimates from the practice's remittances at each payer's rates${x.year === d.next_year ? ` and Medicare's ${esc(x.year)} rules` : ''}, for services you perform yourself and services given incident to your own. Pathology, radiation, drugs and other services you order are not counted: ${app ? "an APP's pay is kept to their own services under the anti-kickback safe harbor for employees" : 'the Stark law does not let a physician\'s productivity pay credit them (a slide you read yourself may count; ask the practice)'}. Biopsies, stages, closures and visit levels follow each patient's needs and are never targets.</p>`;
  };
  body.querySelector('#sheetTarget').oninput = e => { cs.sheetTarget = e.target.value; draw(); };
  body.querySelector('#sheetPrint').onclick = () => window.print();
  draw();
}
