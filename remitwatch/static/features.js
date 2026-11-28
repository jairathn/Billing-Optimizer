/* RemitWatch dashboard: alerts, breakdown explorer, progress scoreboard, feed status and the note-based appeal builder.
   Loaded before the inline script in index.html and uses its helpers ($, api, qs, esc, money, money2, chart, kpi, state, findingsTable, render). */

// a stored moment's date and time where the practice is (today.js practiceTime); before today.js has loaded, UTC's, said so
function atTime(s){ return typeof practiceTime === 'function' ? practiceTime(s) : (String(s || '').replace('T', ' ').slice(0, 16) + (/T\d\d:\d\d/.test(String(s || '')) ? ' UTC' : '')); }

const DIM_LABELS = {provider:'Provider', payer:'Payer', payer_kind:'Payer type', plan:'Plan', billed_code:'Billed code', paid_code:'Paid code',
  code_pair:'Billed → paid', level_drop:'Levels dropped', signal:'Signal', category:'Category', month:'Month paid', quarter:'Quarter paid', year:'Year paid',
  modifier:'Modifier', pos:'Place of service', tier:'Provider type', location:'Location', diagnosis:'Diagnosis', carc:'Reason code', status:'Status',
  code_category:'Service type', action:'Action', handling:'EMA handling'};
const FAMILY_LABELS = {downcoding:'Visit downcoding', code_change:'Procedure code changes', bundling:'Bundling', denial:'Denials', underpayment:'Underpayment', all:'Everything'};
const DIM_FILTER = {provider:'provider', payer:'payer', plan:'plan', billed_code:'billed_code', paid_code:'code', code_pair:'code_pair', signal:'signal', month:'month',
  quarter:'quarter', year:'year', modifier:'modifier', pos:'pos', tier:'tier', location:'location', diagnosis:'diagnosis', carc:'carc', payer_kind:'payer_kind',
  code_category:'code_category', level_drop:'level_drop', action:'actionable', handling:'handling', category:'category', status:'status'};
const FAMILY_BASE = {downcoding:{signal:'DOWNCODE_SVC06,DOWNCODE_VS_BILLED,DOWNCODE_CHARGE,DOWNCODE_LEVEL_REDUCED,DOWNCODE_RATE,DOWNCODE_LINE_SWAP,DOWNCODE_LEVEL_DENIED'},
  code_change:{signal:'CODE_CHANGED'}, bundling:{category:'bundling'}, denial:{category:'denial'}, underpayment:{category:'underpayment'},
  all:{category:'downcoding,bundling,denial,underpayment'}};
const pct = (x, d=1) => x==null ? '–' : (100*x).toFixed(d)+'%';
const per100 = x => x==null ? '–' : (100*x).toFixed(x<0.01?2:1);
const blank = (s, alt) => (s===''||s==null) ? `<span class="muted">${alt||'(none)'}</span>` : esc(s);
const statusScope = () => state.status==='all' ? 'active' : state.status;
function post(path, body){ return api(path, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body||{})}); }

// ---------------------------------------------------------------- header: the health pill and the alert bell
// The answers the header is drawn from; the shell (static/shell.js RWShell.pill, through index.html's shellHeader) says
// them: the feeds and what the app cannot check in dollars, the new alerts, and on the Vercel site when it last analysed
// and that it analyses only on a press (the owner's decision 1).
async function refreshHeader(){
  try {
    const [al, st] = await Promise.all([api('/api/alerts?status=new'), api('/api/sync/status')]);
    state.alertsNew = al.items; state.sync = st;
    if(typeof swingShow === 'function') swingShow($('#swingBar'), al.items);     // a big change in one run: top of every page (swing.js)
    if(typeof shellHeader === 'function') shellHeader();
  } catch(e){ /* older store without these tables */ }
}

// ---------------------------------------------------------------- alerts
const ALERT_KIND = {RAMP:'Ramping up', RISING:'Climbing', RESUMED:'Started again', NEW_BEHAVIOR:'New behavior', PROVIDER_TARGETED:'One provider singled out', RATE_CUT:'Rate cut',
  NEW_REASON_CODE:'New reason code', DEADLINE:'Deadlines', STALE_FEED:'Feed stopped', SWING:'Big change in one run'};
async function alertsTab(v){
  const show = state.filt.alertShow || 'active';
  const res = await api('/api/alerts?status='+show);
  v.innerHTML = `<div class="panel"><h2>Alerts</h2>
    <div class="note">Each run compares the last four weeks of payments with the six months before them, per payer. An alert stays until the pattern stops, then clears itself. If the same pattern returns it is raised again. Dates are measured from the newest remittance in the store, so a late upload does not look like a quiet payer.</div>
    <div class="filters"><select id="alShow"><option value="active" ${show==='active'?'selected':''}>Active</option><option value="cleared" ${show==='cleared'?'selected':''}>Cleared</option><option value="all" ${show==='all'?'selected':''}>All</option></select>
    <span class="muted small">${res.items.length} shown · ${(res.counts.new||0)} new · ${(res.counts.acknowledged||0)} acknowledged · ${(res.counts.cleared||0)} cleared</span></div>
    ${res.items.map(alertCard).join('') || '<div class="muted">Nothing to show.</div>'}</div>`;
  v.querySelector('#alShow').onchange = e => { state.filt.alertShow = e.target.value; render(); };
  v.querySelectorAll('[data-ack]').forEach(b => b.onclick = async () => { await post('/api/alerts/'+b.dataset.ack+'/ack'); await refreshHeader(); render(); });
  v.querySelectorAll('[data-see]').forEach(b => b.onclick = () => { const a = res.items.find(x=>x.alert_id===b.dataset.see); seeAlert(a); });
}
function alertCard(a){
  const d = a.detail || {};
  const facts = [];
  const win = d.recent_window && d.baseline_window ? [`${d.recent_window[0]} to ${d.recent_window[1]}`, `${d.baseline_window[0]} to ${d.baseline_window[1]}`] : ['the recent window', 'the baseline'];
  if(d.recent_count!=null) facts.push(`${d.recent_count} of ${(d.recent_lines||0).toLocaleString()} lines from ${win[0]}`);
  if(d.baseline_count!=null) facts.push(`${d.baseline_count} of ${(d.baseline_lines||0).toLocaleString()} from ${win[1]}`);
  if(d.quiet_days!=null) facts.push(`quiet for ${d.quiet_days} days before that`);
  if(d.signals) facts.push(Object.entries(d.signals).map(([k,n])=>`${k} ${n}`).join(', '));
  if(d.codes) facts.push(d.codes.map(c=>`${c.code} ${money2(c.old_cents)} → ${money2(c.new_cents)} (${pct(c.pct)}) since ${c.since}`).join('; '));
  if(d.by_payer) facts.push(d.by_payer.slice(0,8).map(p=>`${esc(p.payer_group)} ${p.n} / ${money(p.cents)}`).join('; '));
  if(d.note) facts.push(d.note);
  if(a.kind==='SWING'){ if(d.action) facts.push(d.action); if(Array.isArray(d.lines)) facts.push(...d.lines); }
  const can = a.family && a.family!=='feed' && a.family!=='rate' && a.family!=='reason_code' && a.family!=='swing';
  return `<div class="alert ${a.status}"><div class="row1"><span class="chip sev-${a.severity}">${a.severity}</span><span class="chip">${ALERT_KIND[a.kind]||a.kind}</span>
    <span class="muted small">first seen ${typeof practiceDate === 'function' ? practiceDate(a.first_seen) : String(a.first_seen).slice(0,10)} · last checked ${typeof practiceDate === 'function' ? practiceDate(a.last_seen) : String(a.last_seen).slice(0,10)}${a.status==='acknowledged'?' · acknowledged':''}${a.status==='cleared'?' · cleared':''}</span></div>
    <div class="ttl">${esc(a.title)}</div>${facts.length?`<div class="small muted">${facts.map(esc).join(' · ')}</div>`:''}
    <div class="actions" style="margin-top:6px">${can?`<button data-see="${a.alert_id}">See the lines</button>`:''}${a.status==='new'?`<button data-ack="${a.alert_id}">Acknowledge</button>`:''}</div></div>`;
}
function seeAlert(a){
  if(a.kind==='DEADLINE') return goWork({deadline:'month', sort:'deadline'});
  const fam = FAMILY_BASE[a.family] ? a.family : 'all';
  state.tab='explorer';
  state.ex = {family:fam, dim: a.kind==='PROVIDER_TARGETED' ? 'code_pair' : 'provider', dim2:'', payer:a.payer_group||'', provider:a.kind==='PROVIDER_TARGETED'?(a.scope||''):'', from:(a.window_start||'').slice(0,7)};
  render();
}

// ---------------------------------------------------------------- explorer: who and what
async function explorerTab(v){
  const ex = state.ex = Object.assign({family:'downcoding', dim:'provider', dim2:'', payer:'', provider:'', plan:'', code:'', from:'', to:'', metric:'n', pick:null}, state.ex||{});
  const params = {dim:ex.dim, dim2:ex.dim2, family:ex.family, status:statusScope(), year:state.year, payer:ex.payer, provider:ex.provider, plan:ex.plan, code:ex.code, month_from:ex.from, month_to:ex.to};
  const res = await api('/api/breakdown?'+qs(params));
  if(res.rates_locked && state.ownerKey && typeof pvForget === 'function') pvForget();      // the password this page holds is no longer accepted: stop sending it
  if(state.ownerKey && typeof pvArmIdle === 'function') pvArmIdle();                        // looking at these figures counts as use
  const dimOpts = sel => Object.keys(DIM_LABELS).map(k=>`<option value="${k}" ${sel===k?'selected':''}>${DIM_LABELS[k]}</option>`).join('');
  const chips = [['payer','Payer'],['provider','Provider'],['plan','Plan'],['code','Billed code'],['from','From'],['to','To']].filter(([k])=>ex[k])
    .map(([k,l])=>`<span class="chip">${l}: ${esc(k==='provider'?(state.provNames||{})[ex[k]]||ex[k]:ex[k])} <a data-clear="${k}" style="cursor:pointer">✕</a></span>`).join('');
  v.innerHTML = `<div class="panel"><h2>Who and what: ${FAMILY_LABELS[ex.family]}</h2>
    <div class="note">Every count sits next to the number of lines billed, so a busy provider or payer is not mistaken for a targeted one. "Per 100" is findings per 100 ${esc(res.rate_scope)}. Click a row to narrow everything to it, and the findings behind it are listed underneath.
      ${res.rates_locked ? '<span class="sev-medium">Lines billed and rates are left out wherever they would show how busy one clinician is. They are kept for the owner and show here once the Providers tab has been opened with the owner\'s password.</span>' : ''}</div>
    <div class="filters">
      <label class="small">Show <select id="exFam">${Object.keys(FAMILY_LABELS).map(k=>`<option value="${k}" ${ex.family===k?'selected':''}>${FAMILY_LABELS[k]}</option>`).join('')}</select></label>
      <label class="small">by <select id="exDim">${dimOpts(ex.dim)}</select></label>
      <label class="small">and by <select id="exDim2"><option value="">(nothing)</option>${dimOpts(ex.dim2)}</select></label>
      <label class="small">from <input id="exFrom" type="month" value="${esc(ex.from)}"></label><label class="small">to <input id="exTo" type="month" value="${esc(ex.to)}"></label>
      ${ex.dim2?`<label class="small">cells <select id="exMetric"><option value="n" ${ex.metric==='n'?'selected':''}>findings</option><option value="cents" ${ex.metric==='cents'?'selected':''}>dollars</option><option value="rate" ${ex.metric==='rate'?'selected':''}>per 100 lines</option></select></label>`:''}
      ${chips}
      <span class="muted small">${res.total.n.toLocaleString()} findings · ${money(res.total.cents)}${res.total_lines?` · ${res.total_lines.toLocaleString()} ${esc(res.rate_scope)}`:''}</span>
    </div>
    <div id="exBody"></div></div>
    <div class="panel"><h2>The findings behind it</h2><div id="exTbl"></div></div>`;
  const body = v.querySelector('#exBody');
  body.innerHTML = ex.dim2 ? matrixHtml(res, ex) : listHtml(res, ex);
  const set = (k, val) => { state.ex[k]=val; state.page.explorer=0; render(); };
  v.querySelector('#exFam').onchange = e => set('family', e.target.value);
  v.querySelector('#exDim').onchange = e => { state.ex.dim2 = state.ex.dim2===e.target.value ? '' : state.ex.dim2; set('dim', e.target.value); };
  v.querySelector('#exDim2').onchange = e => set('dim2', e.target.value===ex.dim ? '' : e.target.value);
  v.querySelector('#exFrom').onchange = e => set('from', e.target.value);
  v.querySelector('#exTo').onchange = e => set('to', e.target.value);
  if(v.querySelector('#exMetric')) v.querySelector('#exMetric').onchange = e => set('metric', e.target.value);
  v.querySelectorAll('[data-clear]').forEach(a => a.onclick = () => set(a.dataset.clear, ''));
  body.querySelectorAll('[data-k1]').forEach(el => el.onclick = () => drill(ex, el.dataset.k1, el.dataset.k2));
  // findings list for the current scope
  const base = Object.assign({}, FAMILY_BASE[ex.family]);
  if(ex.payer) base.payer = ex.payer; if(ex.provider) base.provider = ex.provider; if(ex.plan) base.plan = ex.plan; if(ex.code) base.billed_code = ex.code;
  await findingsTable(v, v.querySelector('#exTbl'), base);
}
function keyLabel(r, i){ const k = r['k'+i]; return r['label'+i] || (k===''||k==null ? '(none)' : String(k)); }
function drill(ex, k1, k2){
  const apply = (dim, k) => {
    if(dim==='payer') state.ex.payer=k; else if(dim==='provider') state.ex.provider=k; else if(dim==='plan') state.ex.plan=k; else if(dim==='billed_code') state.ex.code=k;
    else if(dim==='month'){ state.ex.from=k; state.ex.to=k; }
    else { state.filt.explorer = Object.assign({}, state.filt.explorer||{}, {}); }
  };
  apply(ex.dim, k1); if(k2!==undefined && ex.dim2) apply(ex.dim2, k2);
  const next = ['provider','payer','plan','billed_code','code_pair','modifier','month'].find(d => d!==ex.dim && d!==ex.dim2 && !({payer:state.ex.payer, provider:state.ex.provider, plan:state.ex.plan, billed_code:state.ex.code}[d]));
  if(['payer','provider','plan','billed_code','month'].includes(ex.dim)) state.ex.dim = next || 'code_pair';
  state.ex.dim2 = ''; state.page.explorer = 0; render();
}
function listHtml(res, ex){
  const max = Math.max(1, ...res.rows.map(r=>r.cents));
  const hasRate = res.denominator_for >= 1;
  return `<table><thead><tr><th>${DIM_LABELS[ex.dim]}</th><th class="num">Findings</th>${hasRate?'<th class="num">Lines billed</th><th class="num">Per 100</th>':''}<th class="num">Dollars</th><th style="width:16%"></th><th class="num">Open</th><th class="num">Still appealable</th><th class="num">Recovered</th><th class="num">Lost</th></tr></thead>
    <tbody>${res.rows.map(r=>`<tr class="row" data-k1="${esc(r.k1==null?'':r.k1)}"><td>${blank(keyLabel(r,1))}${ex.dim==='provider'&&r.k1?` <span class="muted small">${esc(r.k1)}</span>`:''}</td><td class="num">${r.n.toLocaleString()}</td>
      ${hasRate?`<td class="num">${r.lines==null?'–':r.lines.toLocaleString()}</td><td class="num"><b>${per100(r.rate)}</b></td>`:''}
      <td class="num">${money(r.cents)}</td><td><div class="bar"><i style="width:${Math.round(100*r.cents/max)}%"></i></div></td>
      <td class="num">${money(r.open_cents)}</td><td class="num">${money(r.live_cents)}</td><td class="num">${money(r.recovered_cents)}</td><td class="num">${r.lost_n||0}</td></tr>`).join('') || '<tr><td colspan="10" class="muted">No findings in this view.</td></tr>'}</tbody></table>`;
}
function matrixHtml(res, ex){
  const rowTot = {}, colTot = {}, cell = {}, lab1 = {}, lab2 = {};
  res.rows.forEach(r => { const a=String(r.k1==null?'':r.k1), b=String(r.k2==null?'':r.k2); rowTot[a]=(rowTot[a]||0)+r.cents; colTot[b]=(colTot[b]||0)+r.cents; cell[a+'\u0001'+b]=r; lab1[a]=keyLabel(r,1); lab2[b]=keyLabel(r,2); });
  const timeDims = ['month','quarter','year'];
  const order = (tot, dim, n) => timeDims.includes(dim) ? Object.keys(tot).sort().slice(-n) : Object.keys(tot).sort((x,y)=>tot[y]-tot[x]).slice(0,n);
  const rowsK = order(rowTot, ex.dim, 25), colsK = order(colTot, ex.dim2, 12);
  const val = r => !r ? null : (ex.metric==='cents' ? r.cents : (ex.metric==='rate' ? r.rate : r.n));
  const all = rowsK.flatMap(a=>colsK.map(b=>val(cell[a+'\u0001'+b]))).filter(x=>x!=null); const max = Math.max(1e-9, ...all);
  const show = x => x==null ? '' : (ex.metric==='cents' ? money(x) : (ex.metric==='rate' ? per100(x) : x.toLocaleString()));
  return `<div style="overflow:auto"><table class="heat"><thead><tr><th>${DIM_LABELS[ex.dim]} \\ ${DIM_LABELS[ex.dim2]}</th>${colsK.map(b=>`<th class="num">${esc(lab2[b])}</th>`).join('')}</tr></thead>
    <tbody>${rowsK.map(a=>`<tr><td style="text-align:left">${esc(lab1[a])}</td>${colsK.map(b=>{ const r=cell[a+'\u0001'+b], x=val(r); if(x==null) return '<td class="muted">·</td>';
      return `<td class="row" data-k1="${esc(a)}" data-k2="${esc(b)}" title="${r.n} findings, ${money(r.cents)}${r.lines?', '+r.lines.toLocaleString()+' lines':''}" style="cursor:pointer;background:color-mix(in srgb, var(--accent) ${Math.round(8+50*x/max)}%, transparent)">${show(x)}</td>`; }).join('')}</tr>`).join('')}</tbody></table></div>
    ${ex.metric==='rate'&&res.denominator_for<2?'<div class="note">Lines billed are not tracked for this pair of dimensions, so a rate cannot be shown; switch cells to findings or dollars.</div>':''}`;
}

// panels on the Downcoding tab
async function downcodeWho(el, o){
  o = o || {};
  const p = {family:'downcoding', status: o.status || statusScope(), year: o.year != null ? o.year : state.year};
  const [prov, plan, pair, q, mx] = await Promise.all(['provider','plan','code_pair','quarter'].map(d=>api('/api/breakdown?'+qs(Object.assign({dim:d}, p)))).concat([api('/api/breakdown?'+qs(Object.assign({dim:'provider', dim2:'payer'}, p)))]));
  state.provNames = Object.fromEntries(prov.rows.map(r=>[r.k1, r.label1]));
  const tbl = (res, label, n=8) => { const rate = res.denominator_for >= 1;
    return `<table><thead><tr><th>${label}</th><th class="num">Lines</th>${rate?'<th class="num">Per 100 visits</th>':''}<th class="num">Dollars</th></tr></thead><tbody>${res.rows.slice(0,n).map(r=>`<tr><td>${blank(keyLabel(r,1))}</td><td class="num">${r.n}</td>${rate?`<td class="num">${per100(r.rate)}</td>`:''}<td class="num">${money(r.cents)}</td></tr>`).join('')}</tbody></table>`; };
  const qr = q.rows.slice().sort((a,b)=>String(a.k1).localeCompare(String(b.k1)));
  el.innerHTML = `<div class="grid two">
    <div class="panel"><h2>Who is being downcoded</h2>${tbl(prov,'Provider')}</div>
    <div class="panel"><h2>Which plans do it</h2>${tbl(plan,'Plan on the remit')}</div>
    <div class="panel"><h2>What gets cut</h2>${tbl(pair,'Billed → paid')}</div>
    <div class="panel"><h2>Rate by quarter paid</h2><canvas id="cDcQ"></canvas></div></div>
    <div class="panel"><h2>Provider by payer</h2><div class="note">Downcoded visits per cell. Open "Who & what" for any other cut of the data.</div>${matrixHtml(mx, {dim:'provider', dim2:'payer', metric:'n'})}</div>`;
  chart('cDcQ', {type:'bar', data:{labels:qr.map(r=>r.k1), datasets:[{type:'line', label:'per 100 visits', yAxisID:'y1', borderColor:palette[1], backgroundColor:palette[1], tension:.2, data:qr.map(r=>r.rate==null?null:100*r.rate)},
    {label:'downcoded visits', backgroundColor:palette[0], data:qr.map(r=>r.n)}]},
    options:{plugins:{legend:{position:'bottom'}}, scales:{y:{beginAtZero:true,title:{display:true,text:'visits'}}, y1:{position:'right',beginAtZero:true,grid:{drawOnChartArea:false},title:{display:true,text:'per 100 visits'}}}}});
}

// ---------------------------------------------------------------- progress
async function progressTab(v){
  const p = await api('/api/progress');
  const st = Object.fromEntries(p.pipeline.map(r=>[r.status, r]));
  const g = k => st[k] || {n:0, cents:0, recovered_cents:0};
  const identified = p.pipeline.filter(r=>r.status!=='dismissed').reduce((a,r)=>a+r.cents,0);
  v.innerHTML = `<div class="page-head"><h1 class="page-title">Progress</h1><span class="muted small">What came back after a fix or appeal, what is being worked, and what is still open.</span></div>
    <div class="grid kpis">
    ${kpi('Paid back', money(g('resolved').recovered_cents), g('resolved').n.toLocaleString()+' items paid after rework', '')}
    ${kpi('Being worked', money(g('appealed').cents+g('in_progress').cents), (g('appealed').n+g('in_progress').n).toLocaleString()+' sent or started', 'working')}
    ${kpi('Window open', money(p.live.cents), p.live.n.toLocaleString()+' items you can still get back', 'open')}
    ${kpi('Window closed', money(p.expired.cents), p.expired.n.toLocaleString()+' items (filing rules partly estimated)', 'closed')}
    ${g('lost').n ? kpi('Lost', money(g('lost').cents), g('lost').n.toLocaleString()+' upheld by the payer', '') : ''}
    ${g('done').n ? kpi('Marked done', money(g('done').cents), g('done').n.toLocaleString()+' closed by a person with nothing from the payer (the patient billed, or given up)', '') : ''}</div>
    <div class="grid two">
      <div class="panel"><h2>Recovered by month</h2><div class="note">Money paid on a later remittance for lines that were first denied, bundled or cut. Counted automatically when the payment arrives.</div><canvas id="cRec"></canvas></div>
      <div class="panel"><h2>Share recovered, by month of the first payment</h2><div class="note">Of what went wrong in each month, how much has been won back so far. Recent months are still in play.</div><canvas id="cCoh"></canvas></div>
    </div>
    <div class="panel"><h2>Downcoded visits per 100, by payer</h2><div class="note">The fight in one chart. A payer's line going to zero means it stopped; a line coming back is what the alerts watch for.</div><canvas id="cDcRate"></canvas></div>
    <div class="panel"><h2>By payer</h2><table><thead><tr><th>Payer</th><th class="num">Findings</th><th class="num">Identified</th><th class="num">Recovered</th><th class="num">Paid later</th><th class="num">Avg days to payment</th><th class="num">Being worked</th><th class="num">Lost</th><th class="num">Open</th><th class="num">Still appealable</th></tr></thead>
      <tbody>${p.by_payer.map(r=>`<tr class="row" data-p="${esc(r.payer_group)}"><td>${esc(r.payer_group)}</td><td class="num">${r.n.toLocaleString()}</td><td class="num">${money(r.cents)}</td><td class="num">${money(r.recovered_cents)}</td><td class="num">${pct(r.resolved_n/r.n,0)}</td><td class="num">${r.avg_days==null?'–':r.avg_days}</td><td class="num">${r.working_n}</td><td class="num">${r.lost_n}</td><td class="num">${money(r.open_cents)}</td><td class="num">${money(r.live_cents)}</td></tr>`).join('')}</tbody></table></div>
    <div class="panel"><h2>Activity</h2><table class="small"><thead><tr><th>When</th><th>What</th><th>Payer</th><th>Finding</th><th class="num">Amount</th><th>By</th></tr></thead>
      <tbody>${p.events.map(e=>`<tr class="row" data-id="${esc(e.finding_id)}"><td>${esc(atTime(e.at))}</td><td>${eventWhat(e)}${e.kind==='paid_on_later_remit'?' (paid on a later remit)':''}</td><td>${esc(e.payer_group||'')}</td><td>${esc(e.signal||'')} ${esc(e.procedure_code||'')}</td><td class="num">${money2(e.amount_cents)}</td><td>${esc(e.actor||'')}</td></tr>`).join('') || '<tr><td colspan="6" class="muted">No status changes yet. Mark findings appealed, resolved or lost from their detail panel and they show up here.</td></tr>'}</tbody></table></div>`;
  // each card opens the Worklist list of its own number
  const KPI_GO = {working:{status:'in_progress,appealed', deadline:'', sort:'deadline'}, open:{deadline:'valid', sort:'deadline'}, closed:{deadline:'expired', sort:'deadline'}};
  v.querySelectorAll('.kpi').forEach(k=>k.onclick=()=>{ const go = KPI_GO[k.dataset.go]; if(go) goWork(go); });
  v.querySelectorAll('tr.row[data-p]').forEach(tr=>tr.onclick=()=>{ state.tab='explorer'; state.ex={family:'all', dim:'signal', dim2:'', payer:tr.dataset.p}; render(); });
  v.querySelectorAll('tr.row[data-id]').forEach(tr=>tr.onclick=()=>openDetail(tr.dataset.id));
  const cats = ['denial','bundling','downcoding','underpayment'];
  const months = [...new Set(p.recovered_by_month.map(r=>r.month))].sort();
  chart('cRec', {type:'bar', data:{labels:months, datasets:cats.map((c,i)=>({label:c, backgroundColor:palette[i], data:months.map(m=>{ const r=p.recovered_by_month.find(x=>x.month===m&&x.category===c); return r?r.cents/100:0; })}))},
    options:{scales:{x:{stacked:true}, y:{stacked:true, ticks:{callback:x=>'$'+x.toLocaleString()}}}, plugins:{legend:{position:'bottom'}}}});
  chart('cCoh', {type:'bar', data:{labels:p.cohorts.map(r=>r.month), datasets:[{label:'recovered', backgroundColor:palette[3], data:p.cohorts.map(r=>r.n?100*r.resolved_n/r.n:0)},
    {label:'being worked', backgroundColor:palette[2], data:p.cohorts.map(r=>r.n?100*r.working_n/r.n:0)}, {label:'lost', backgroundColor:palette[7], data:p.cohorts.map(r=>r.n?100*r.lost_n/r.n:0)}]},
    options:{scales:{x:{stacked:true}, y:{stacked:true, max:100, ticks:{callback:x=>x+'%'}, title:{display:true,text:'share of findings'}}}, plugins:{legend:{position:'bottom'}}}});
  const tot = {}; p.downcode_trend.forEach(r=>{ tot[r.payer_group]=(tot[r.payer_group]||0)+r.n; });
  const top = Object.keys(tot).sort((a,b)=>tot[b]-tot[a]).slice(0,5);
  const lines = {}; p.visit_lines.forEach(r=>{ lines[r.month+'|'+r.payer_group]=r.lines; });
  const ms = [...new Set(p.visit_lines.map(r=>r.month))].sort();
  chart('cDcRate', {type:'line', data:{labels:ms, datasets:top.map((g,i)=>({label:g, borderColor:palette[i], backgroundColor:palette[i], tension:.2, pointRadius:2, spanGaps:true,
    data:ms.map(m=>{ const n=(p.downcode_trend.find(x=>x.month===m&&x.payer_group===g)||{}).n||0; const l=lines[m+'|'+g]; return l&&l>=20 ? 100*n/l : null; })}))},
    options:{plugins:{legend:{position:'bottom'}}, scales:{y:{beginAtZero:true, title:{display:true, text:'downcoded per 100 office visit lines'}}}}});
}

// ---------------------------------------------------------------- feed status (Data tab)
async function feedPanel(el){
  const s = await api('/api/sync/status'); const lc = s.last_cycle || {};
  const cloud = s.mode === 'serverless', mem = s.memory_mb || {};
  const fmtInt = n => (Number(n)||0).toLocaleString('en-US');
  const num = v => esc(String(Number(v)||0));
  const yes = (b, t, f) => b ? `<span class="ok">${t}</span>` : `<span class="sev-medium">${f}</span>`;
  el.innerHTML = `<div class="panel"><h2>Feed</h2><table>
    <tr><td>Dropbox connection</td><td>${yes(s.dropbox_configured,'connected','not configured: set DROPBOX_APP_KEY, DROPBOX_APP_SECRET and DROPBOX_REFRESH_TOKEN')}</td></tr>
    ${cloud ? `<tr><td>Analysis</td><td>only when someone presses Run the analysis now (below, in Analysis on this site)${s.analysed && s.analysed.at ? `; last analysed ${esc(String(s.analysed.at).replace('T',' ').slice(0,16))} UTC` : ''}${s.cron_ready ? '' : ' · <span class="sev-medium">off: CRON_SECRET is not set</span>'} · <a href="/setup">setup checklist</a></td></tr>
    ${s.storage_error?`<tr><td>Storage</td><td><span class="sev-high">${esc(s.storage_error)}</span></td></tr>`:''}
    ${s.stalled_since?`<tr><td>Last run</td><td><span class="sev-high">A run that started ${esc(String(s.stalled_since).replace('T',' ').slice(0,16))} UTC stopped without finishing (out of memory or time). No run comes here without a press: press Run the analysis now (below, in Analysis on this site) to carry on from where it stopped.</span></td></tr>`:''}
    ${(lc.ingest&&lc.ingest.failed&&lc.ingest.failed.length)?`<tr><td>Files the last run could not use</td><td><span class="sev-medium">${lc.ingest.failed.map(esc).join(', ')}</span><div class="small muted">Each is skipped until it changes in Dropbox. Replace the file there to have it read.</div></td></tr>`:''}
    ${lc.halted?`<tr><td>Last run</td><td><span class="sev-high">Stopped after ${esc(lc.halted)}. Press Run the analysis now (below, in Analysis on this site) to carry on.</span></td></tr>`:''}
    ${mem.peak?`<tr><td>Memory used by the last run</td><td>${fmtInt(mem.peak)} MB at its peak${mem.limit?` of ${fmtInt(mem.limit)} MB available`:''}</td></tr>`:''}`
    : `<tr><td>Automatic sync</td><td>${s.interval_minutes?`every ${s.interval_minutes} minutes${s.next_run?', next about '+s.next_run.replace('T',' ').slice(0,16)+' UTC':''}`:'off (set REMITWATCH_SYNC_INTERVAL_MINUTES, for example 60)'} ${s.scheduler_running?'<span class="ok">· scheduler running</span>':''}</td></tr>`}
    ${cloud ? '' : `<tr><td>Instant sync on upload</td><td>${yes(s.webhook_ready,'ready: point the Dropbox app webhook at /api/dropbox/webhook','needs DROPBOX_APP_SECRET')}</td></tr>`}
    <tr><td>Newest payment in the store</td><td>${esc(s.newest_payment_date||'–')} ${s.days_behind!=null?`(${s.days_behind} days ago)`:''}</td></tr>
    <tr><td>Last new remittance file</td><td>${s.last_remit_ingested ? esc(atTime(s.last_remit_ingested)) : '–'}</td></tr>
    <tr><td>Last cycle</td><td>${lc.finished?`${esc(String(lc.finished).replace('T',' ').slice(0,16))} UTC (${esc(lc.trigger||'')})${lc.error?' <span class="sev-high">failed: '+esc(lc.error)+'</span>':''} · ${lc.ingest?`${num(lc.ingest.remits)} remits, ${num(lc.ingest.ema)} EMA exports, ${num(lc.ingest.claims_837)} claim files`:''}${lc.alerts?` · ${num(lc.alerts.new)} new alerts`:''}`:'never'}</td></tr>
    <tr><td>Email (alerts and weekly summary)</td><td>${yes(s.smtp_configured,'configured','not configured: set SMTP_HOST and REPORT_TO')}</td></tr>
    <tr><td>Alert webhook</td><td>${s.alert_webhook?'<span class="ok">configured</span>':'off (optional: ALERT_WEBHOOK_URL)'}</td></tr>
    <tr><td>Clinical note review</td><td>${yes(s.note_review,'on','off: set ANTHROPIC_API_KEY to turn it on; packets are still built from the template without it')}</td></tr>
    </table>${cloud ? '' : `<div class="actions" style="margin-top:8px"><button class="primary" id="syncNow" ${s.running?'disabled':''}>${s.running?'Running…':'Sync and analyse now'}</button><span class="small muted">Pulls new files from Dropbox, ingests them, re-runs detection and the alert checks. Safe to press at any time.</span></div>`}</div>`;
  const sn = el.querySelector('#syncNow');     // on the Vercel site the press is in Analysis on this site (ops.js)
  if(sn) sn.onclick = async e => { e.target.disabled = true; e.target.textContent = 'Started…'; await post('/api/sync/run'); setTimeout(()=>{ refreshHeader(); feedPanel(el); }, 4000); };
}

// ---------------------------------------------------------------- finding detail: facts, history, note-based appeal builder
function drawerExtras(f, r){
  // What EMA shows paid on an item no remittance shows paid. A whole claim EMA shows partly paid stays open (detect.py),
  // and its codes paid and not paid are named: marking it recovered on the codes paid would drop the rest from every list.
  const emaPaidNote = e => {
    const src = `${e.date ? ' on ' + esc(e.date) : ''}, ${esc(e.source || 'EMA')}`;
    if((e.codes_not_paid||[]).length) return `<div class="note"><b>EMA shows part of this claim paid.</b> Insurance paid ${esc((e.codes_paid||[]).join(', ') || 'some of its codes')} (${money2(e.paid_cents)}${src}) but not ${esc(e.codes_not_paid.join(', '))}. The item stays open until every code is paid. Check EMA, and mark it recovered only if the rest was paid too.</div>`;
    return `<div class="note"><b>EMA shows this line paid.</b> ${money2(e.paid_cents)} from insurance${e.date?' on '+esc(e.date):''} (${esc(e.source||'EMA')}), but no remittance in the files shows that payment. Check the payment in EMA; if it is real, mark this recovered. If the remittance for it is missing, put it in the Dropbox folder.</div>`;
  };
  const facts = [['Billed code', f.billed_code], ['Billed → paid', f.code_pair], ['Plan on the remit', f.plan], ['Place of service', f.pos_class], ['Location', f.location], ['Diagnosis', f.diagnosis],
    ['Modifier', f.modifier_class], ['Recovered', f.recovered_cents!=null ? money2(f.recovered_cents)+' on '+(f.recovered_date||'') : null], ['Appealed', f.appealed_at ? (typeof practiceDate === 'function' ? practiceDate(f.appealed_at) : String(f.appealed_at).slice(0,10)) : null]].filter(x=>x[1]!=null && x[1]!=='');
  const ev = f.evidence || {};
  const why = f.signal==='SECOND_RATE' && ev.note ? `<div class="note"><b>Likely reason.</b> ${esc(ev.note)}</div>` : '';
  // today as the lists count it (siteDay: the practice's day, from the brief), else the reader's own: a window open until
  // the end of its last day is still open that evening
  const now = new Date(), today = typeof siteDay === 'function' ? siteDay() : `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')}`;
  const dl = f.signal==='SECOND_RATE' && Array.isArray(ev.deadlines) ? ev.deadlines.filter(x=>Array.isArray(x)) : [];
  const dated = dl.filter(x=>x[0]).sort((a,b)=>a[0]<b[0]?-1:1), live = dated.filter(x=>x[0]>=today);
  const listed = dl.reduce((a,x)=>a+(x[1]||0),0), unknown = dl.filter(x=>!x[0]).reduce((a,x)=>a+(x[1]||0),0) + Math.max((ev.lines||0) - listed, 0);
  const sumN = xs => xs.reduce((a,x)=>a+(x[1]||0),0), sumC = xs => xs.reduce((a,x)=>a+(x[2]||0),0);
  const windows = dl.length || unknown ? `<div class="note"><b>Filing windows.</b> ${live.length
      ? `${sumN(live)} of its ${esc(ev.lines||sumN(dl))} lines (${money2(sumC(live))} of the gap) are still inside their windows today; the next closes on <b>${esc(live[0][0])}</b>.`
      : dated.length ? `No line with a known deadline is still inside its window; the last closed on ${esc(dated[dated.length-1][0])}.` : ''}${unknown
      ? ` ${unknown} line${unknown===1?' has':'s have'} no payment date on the remittance, so ${unknown===1?'its window is':'their windows are'} unknown.` : ''}</div>` : '';
  const pl = ev.paid_later || {}, plText = `${money2(pl.paid_cents)} on ${esc(pl.payment_date||'')}${pl.claim ? ' under claim '+esc(pl.claim) : ''}`;
  const later = ev.paid_later && f.status!=='resolved' ? `<div class="note"><b>A later payment was found:</b> ${plText}. It was not applied because a person set this finding to ${esc(String(f.status).replace('_',' '))}. If it covers this line, mark it recovered.</div>`
    : ev.paid_later ? `<div class="note">${esc(f.auto_note || 'Paid later.')}</div>`
    : ev.partly_paid ? `<div class="note"><b>Partly paid.</b> Claim ${esc(ev.partly_paid.claim)} paid ${esc(ev.partly_paid.units_paid)} of the ${esc(ev.partly_paid.units_billed)} units billed (${money2(ev.partly_paid.paid_cents)} on ${esc(ev.partly_paid.payment_date)}). The rest is still unpaid.</div>`
    : ev.adjudicated_elsewhere ? `<div class="note"><b>Processed on another claim.</b> Claim ${esc(ev.adjudicated_elsewhere.claim)} for this patient, date and code was processed on ${esc(ev.adjudicated_elsewhere.payment_date)} with ${money2(ev.adjudicated_elsewhere.patient_owes_cents)} owed by the patient (deductible, coinsurance or copay). If that is this service, the payer has decided it.</div>`
    : ev.maybe_paid_later ? `<div class="note"><b>Possibly paid on another claim.</b> Claim ${esc(ev.maybe_paid_later.claim)} for this patient and date paid ${esc((ev.maybe_paid_later.codes_paid||[]).join(', '))} on ${esc(ev.maybe_paid_later.payment_date)}. It was not closed automatically because ${esc(ev.maybe_paid_later.why_not_closed)}. If that claim covers this line, mark it recovered.</div>`
    : ev.ema_shows_paid ? emaPaidNote(ev.ema_shows_paid) : '';
  const claims = f.signal==='SECOND_RATE' && (ev.claims||[]).length ? `<h3 class="small">Claims paid at this amount${ev.lines > ev.claims.length ? ' (first '+ev.claims.length+' of '+ev.lines+')' : ''}</h3>
    <table class="small"><thead><tr><th>EMA claim ID</th><th>Date of service</th><th class="num">Allowed</th><th>File by</th></tr></thead><tbody>${ev.claims.map(c=>`<tr><td>${c[0] ? `<a href="#" class="claim-link" data-claim="${esc(c[0])}" title="Every remittance for this claim">${esc(c[0])}</a>` : ''}</td><td>${esc(c[1])}</td><td class="num">${money2(c[2])}</td><td class="${c[3] && c[3] < today ? 'muted' : ''}">${c[3] ? esc(c[3]) + (c[3] < today ? ' (passed)' : '') : '<span class="muted">unknown</span>'}</td></tr>`).join('')}</tbody></table>` : '';
  // what bears on the next step stays in view; the facts and the note builder go in the drawer's folds
  const openWindow = !f.appeal_deadline || f.appeal_deadline >= today;
  // the same rule as Today's step and the list it opens: a filing date on file that has not passed
  const site = typeof siteDay === 'function' ? siteDay() : today;       // the day the lists use, not the browser's
  const datedOpen = !!f.appeal_deadline && f.appeal_deadline >= site;
  const wroteOff = f.written_off_cents ? `<div class="note sev-medium"><b>Written off in EMA.</b> ${money2(f.written_off_cents)} of the money in dispute on this ${f.line_key ? 'line' : 'claim'} is written off (or moved to the patient) in EMA${f.written_off_on ? ', as of ' + esc(f.written_off_on) : ''}${!['open','in_progress'].includes(f.status) ? '.' : datedOpen ? '. The payer can still be asked for it: work it, and reverse the write-off in EMA when the payer pays.' : f.appeal_deadline ? '. Its filing window has closed.' : '. No filing date is on file for it: check the payer\'s rules before working it.'}</div>` : '';
  const notes = `${f.stale?'<div class="note sev-medium">The current rules no longer produce this finding. It is kept because someone worked it.</div>':''}${wroteOff}${why}${windows}${later}`;
  const history = (r.events||[]).length?`<h3 class="small">History</h3><table class="small"><tbody>${r.events.map(e=>`<tr><td>${esc(atTime(e.at))}</td><td>${eventWhat(e)}</td><td>${money2(e.amount_cents)}</td><td>${e.by ? 'by ' + esc(e.by) : esc(e.actor||'')}</td><td>${esc(e.note||'')}</td></tr>`).join('')}</tbody></table>`:'';
  const factsHtml = `${claims}
    <table class="small">${facts.map(([k,val])=>`<tr><td>${k}</td><td>${esc(val)}</td></tr>`).join('')}
      <tr><td>Filing window</td><td>${f.appeal_deadline?'by <b>'+esc(f.appeal_deadline)+'</b>. ':''}${esc(f.deadline_basis||'')}</td></tr></table>${history}`;
  const ema = typeof state !== 'undefined' && !!(state.meta && (state.meta.modmed || state.meta.uploads));     // no page state when evaluated on its own
  const byHand = ema && !state.meta.modmed;         // no API yet: the note uploaded from EMA on the Upload page
  const builder = f.claim_key ? `${ema ? `<div class="filters" style="margin-bottom:6px"><button id="noteEma">${byHand ? 'Use the note uploaded from EMA and build the packet' : 'Get the note from EMA and build the packet'}</button>${byHand ? ' <a class="small" href="/upload">Upload notes</a>' : ''}</div><div id="emaMsg" class="small muted"></div>` : ''}<div class="note">${ema ? 'Or attach' : 'Attach'} the note for this date of service (PDF, Word or text) or paste it. The review checks the note against the CPT rules that decide this dispute, quotes it word for word, and tells you plainly when the note does not support an appeal. The result is one PDF: letter, review, remittance evidence and the note.</div>
      <div class="filters"><input type="file" id="noteFile" accept=".pdf,.docx,.txt,.rtf,.html,.htm"><label class="small"><input type="checkbox" id="noteAi" checked> review with Claude</label><button id="noteGo">Build packet</button></div>
      <textarea id="noteText" rows="4" placeholder="…or paste the note text here" style="width:100%"></textarea>
      <div id="noteOut">${(r.packets||[]).length?`<div class="small"><b>Earlier packets:</b> ${r.packets.map(p=>{
        const day = `${typeof practiceDate === 'function' ? practiceDate(p.created_at) : String(p.created_at).slice(0,10)} ${esc(p.note_name||'')}`;
        return p.note_hold ? `${day} <button class="ghost small" data-pk-hold="${esc(p.packet_id)}">Check the note first</button>`
          : `<a href="/api/packets/${encodeURIComponent(p.packet_id)}/pdf">${day}</a>${p.emailed_to?' (emailed to '+esc(p.emailed_to)+')':''}`;
      }).join(' · ')}</div>`:''}</div>` : '';
  return {notes, facts: factsHtml, builder};
}
// One line of an item's history (its panel, and Activity on Progress): a status change, from → to, or what happened with
// no change of status: a fax, the visit note looked up by "Build packets from EMA" (none found, or a person has to choose),
// or a packet built by it (on a serverless host), a change pressed on a page drawn before the run changed the item (kept out).
const EVENT_WORDS = {fax:'Fax', note_lookup:'Note lookup', packet:'Packet built', note_check:'Note checked by a person', not_applied:'Not applied',
  asked:'Asked the payer'};
function eventWhat(e){
  return EVENT_WORDS[e.kind] ? `<b>${EVENT_WORDS[e.kind]}</b>` : `${esc(e.from_status||'')} → <b>${esc(e.to_status||'')}</b>`;
}
const REC_LABEL ={appeal:['Appeal','ok'], appeal_with_caution:['Appeal with caution','sev-medium'], do_not_appeal:['Do not appeal','sev-high'], correct_and_resubmit:['Correct and resubmit','sev-medium'], review_manually:['Review by hand','muted']};
function packetHtml(p){
  const a = p.analysis || {}; const rec = REC_LABEL[a.recommendation] || [a.recommendation||'', 'muted'];
  const el = (label, e) => (!e || e.level==='not_applicable') ? '' : `<tr><td>${label}</td><td><b>${esc(e.level)}</b></td><td>${esc(e.rationale)}${(e.quotes||[]).map(q=>`<div class="muted"><i>“${esc(q)}”</i></div>`).join('')}</td></tr>`;
  const m = a.modifier_25 || {};
  return `<div class="panel" style="margin-top:8px"><div><span class="chip ${rec[1]}"><b>${esc(rec[0])}</b></span> <span class="small">${esc(a.recommendation_reason||a.reason||'')}</span></div>
    ${a.mode==='ai'?`<div class="small" style="margin:6px 0">${esc(a.visit_summary||'')}</div>
      <table class="small"><thead><tr><th>Element</th><th>Level</th><th>Basis in the note</th></tr></thead><tbody>${el('Problems addressed', a.problems)}${el('Data reviewed', a.data)}${el('Risk of management', a.risk)}
      ${m.relevant?`<tr><td>Separate from the procedure</td><td><b>${esc(m.separately_identifiable)}</b></td><td>${esc(m.rationale)}${(m.quotes||[]).map(q=>`<div class="muted"><i>“${esc(q)}”</i></div>`).join('')}</td></tr>`:''}</tbody></table>
      <div class="small">MDM <b>${esc(a.mdm_level||'')}</b> · time documented: ${esc(a.time_documented||'none')} · level supported by the note: <b>${esc(a.supported_code||'')}</b> · supports the billed level: <b>${esc(a.supports_billed||'')}</b></div>
      ${(a.gaps||[]).length?`<div class="small"><b>Gaps:</b> ${a.gaps.map(esc).join(' · ')}</div>`:''}${(a.documentation_tips||[]).length?`<div class="small"><b>For next time:</b> ${a.documentation_tips.map(esc).join(' · ')}</div>`:''}`:''}
    <div class="actions" style="margin-top:8px"><a href="/api/packets/${p.packet_id}/pdf"><button class="primary">Download the packet (PDF)</button></a><button id="pkCopy">Copy letter</button>
      <input id="pkTo" placeholder="email the PDF to…" style="min-width:220px"><button id="pkSend">Send</button><span id="pkMsg" class="small muted"></span></div>
    ${(p.checklist||[]).length ? `<div class="note sev-medium"><b>Before you send it</b>${p.checklist.map(c => `<div>${esc(c)}</div>`).join('')}</div>` : ''}
    <div class="note">The PDF contains the patient's name, member ID and the note. Email it only to a mailbox inside the practice or a payer address that accepts appeals.</div>
    ${p.corrected_claim_instructions?`<div class="small"><b>Correction instead of appeal:</b> ${esc(p.corrected_claim_instructions)}</div>`:''}
    <pre>${esc(p.letter)}</pre></div>`;
}
// A packet whose note's text does not show this patient's visit waits for a person (api._note_hold, the rule the fax
// send holds by): one notice in place of the packet's buttons, the note opened in the browser to look at, and the tick that
// lets it go. Until then the server refuses its download and its email too (review d-1).
function noteHoldHtml(p){
  const h = p.note_hold || {}, f = (typeof state !== 'undefined' && state.detailF) || {};
  const day = h.visit_date || f.service_date || '';
  const when = day ? (typeof longDate === 'function' ? longDate(day) : day) : 'the date of service';
  return `<div class="note sev-high" id="pkHold"><b>Check this note before it goes.</b> ${esc(h.why || '')}
    <a href="/api/packets/${encodeURIComponent(p.packet_id)}/pdf?check=1" target="_blank" rel="noopener">Open it</a> and look: if it is
    this patient's visit on ${esc(when)}, tick the box. If it is not, attach this visit's own note above and build the packet again.
    <div class="actions"><label><input type="checkbox" id="pkOk"> I opened this note: it is this patient's visit on ${esc(when)}</label>
    <button id="pkUse" disabled>Use this note</button></div><div id="pkHoldMsg" class="small"></div></div>`;
}
// the person ticked: the panel's copy of the item's packets lets this one go, and the next step's box is drawn again
function xpNoteChecked(packetId){
  const r = typeof state !== 'undefined' ? state.detailR : null;
  (r && r.packets || []).forEach(q => { if(q.packet_id === packetId) q.note_hold = null; });
  const d = document.querySelector('#drawer');
  if(r && d && typeof xpRedrawBox === 'function') xpRedrawBox(state.detailId, d);
}
function showPacket(out, p){
  if(p.note_hold){
    out.innerHTML = noteHoldHtml(p);
    const ok = out.querySelector('#pkOk'), use = out.querySelector('#pkUse');
    ok.onchange = () => { use.disabled = !ok.checked; };
    use.onclick = async () => {
      if(use.disabled || !ok.checked) return;
      use.disabled = true;
      try {
        await post('/api/packets/' + encodeURIComponent(p.packet_id) + '/note-checked', {});
        const full = p.letter ? p : await api('/api/packets/' + encodeURIComponent(p.packet_id));
        p.note_hold = null; full.note_hold = null;
        xpNoteChecked(p.packet_id);
        showPacket(out, full);
      } catch(e){ out.querySelector('#pkHoldMsg').textContent = 'Not saved: ' + e.message; use.disabled = false; }
    };
    return;
  }
  out.innerHTML = packetHtml(p);
  out.querySelector('#pkCopy').onclick = () => navigator.clipboard.writeText(p.letter);
  const send = out.querySelector('#pkSend');            // one email per click: a second click waits for the first answer
  send.onclick = async () => { if(send.disabled) return; send.disabled = true;
    try { const res = await post('/api/packets/'+p.packet_id+'/email', {to: out.querySelector('#pkTo').value}); out.querySelector('#pkMsg').textContent = res.sent ? 'sent to '+res.to : 'not sent: '+res.reason; }
    finally { send.disabled = false; } };
}
// ---------------------------------------------------------------- the visit note behind an appeal, from EMA or uploaded
// Words for what ties a note to the claim's patient (modmed.find_note's identified_by: "EMA patient ID", "medical record
// number", "name", or "a person" when someone chose the note or attached it to the item).
const NOTE_PERSON = 'a person';
const noteAnd = xs => xs.length < 2 ? xs.join('') : `${xs.slice(0, -1).join(', ')} and ${xs[xs.length - 1]}`;
// in the message for a packet built: "patient identified by EMA patient ID and name"
function noteIdWords(ids){
  const all = (ids||[]).filter(Boolean), rest = all.filter(x => x !== NOTE_PERSON), person = all.includes(NOTE_PERSON);
  if(!rest.length) return person ? 'the note chosen by a person' : '';
  return `patient identified by ${noteAnd(rest)}${person ? ', and the note chosen by a person' : ''}`;
}
// over one patient's notes, when more than one patient fits the claim: what of the claim agrees with this patient's record
function noteAgreeWords(ids){
  const all = (ids||[]).filter(Boolean), rest = all.filter(x => x !== NOTE_PERSON);
  if(all.includes(NOTE_PERSON)) return 'attached to this claim by a person';
  return rest.length ? `the claim's ${noteAnd(rest)} ${rest.length > 1 ? 'agree' : 'agrees'}` : 'the name agrees only in part';
}
// who a patient is, only where the site shows names (a candidate's patient): "Sample Pat, born 1980-01-02, ID 12345"
function notePatient(p){
  if(!p) return '';
  const ids = (p.ids||[]).filter(Boolean);
  return [p.name || '', p.birth_date ? 'born ' + p.birth_date : '', ids.length ? `${ids.length > 1 ? 'IDs' : 'ID'} ${ids.join(', ')}` : ''].filter(Boolean).map(esc).join(', ');
}
// The notes a person can pick from. When more than one patient fits the claim the rows come in groups, "Patient 1",
// "Patient 2" (patient_n), each saying who the patient is where names are shown and what of the claim agrees with them.
// A row that may not be this visit's note (a later note that names no visit, a document that is not a visit note, a file
// that does not read: pick_only) says so. o.who false: no line over a single patient's notes.
function notePickList(list, label, o){
  o = o || {};
  const groups = [];
  (list||[]).forEach(c => { const k = Number(c.patient_n) || 1; let g = groups.find(x => x.k === k); if(!g) groups.push(g = {k, rows: []}); g.rows.push(c); });
  if(!groups.length) return '';
  groups.sort((a, b) => a.k - b.k);
  const several = groups.length > 1;
  const row = c => `<tr><td class="day">${esc(c.date||'')}${c.written && c.written !== c.date ? `<div class="muted">written ${esc(c.written)}</div>` : ''}</td>
    <td>${esc(c.type||'Note')}${c.pick_only ? '<div class="sev-medium">check first: may not be this visit\'s note</div>' : ''}</td>
    <td>${esc(c.author||'')}</td><td><button data-ema-pick="${esc(c.id)}" style="white-space:nowrap">${esc(label)}</button></td></tr>`;
  // one table, so the columns line up from one patient to the next; each patient's line heads its own notes
  return `<table class="small" style="margin-top:4px"><tbody>${groups.map(g => {
    const first = g.rows[0], who = notePatient(first.patient);
    const agree = several && Array.isArray(first.identified_by) ? ` · ${esc(noteAgreeWords(first.identified_by))}` : '';
    const head = several || (who && o.who !== false) ? `<tr><td colspan="4" style="padding-top:10px"><b>${several ? 'Patient ' + g.k : 'Patient'}</b>${who ? ': ' + who : ''}${agree}</td></tr>` : '';
    return head + g.rows.map(row).join('');
  }).join('')}</tbody></table>`;
}
// The note for this claim, read from ModMed EMA or uploaded on the Upload page (/api/appeals/from_ema), built into a packet
// as an attached note would be. When a person has to choose (more than one note, only the name agrees, a later note or a
// document that is not a visit note), the notes come back to pick from, grouped by patient. With none to pick from (two
// patients fit and names are switched off here) only the reason shows: the note is attached by hand. The patient's other
// documents stay one click away under a packet built.
async function noteFromEma(id, d, documentId){
  const out = d.querySelector('#noteOut'), msg = d.querySelector('#emaMsg'), btn = d.querySelector('#noteEma'), ai = d.querySelector('#noteAi');
  if(!out) return;
  if(btn) btn.disabled = true;
  msg.textContent = '';
  out.innerHTML = '<div class="muted small">Finding the visit note and building the packet. A review takes about a minute.</div>';
  const bindPicks = el => el.querySelectorAll('[data-ema-pick]').forEach(b => b.onclick = () => noteFromEma(id, d, b.dataset.emaPick));
  try {
    const r = await post('/api/appeals/from_ema', {finding_id: id, use_ai: ai ? ai.checked : true, document_id: documentId || null});
    if(r.status === 'built'){
      showPacket(out, r.packet);
      xpPacketBuilt(id, d, r.packet);
      const n = r.note || {};
      const what = `${esc(n.type||'note')}${n.date?' of '+esc(n.date):''}`;
      const how = noteIdWords(r.identified_by || n.identified_by) || (r.found_by ? 'patient found by ' + r.found_by : '');
      msg.innerHTML = (r.source === 'upload' ? `Built from the note uploaded from EMA${n.uploaded_name ? ' (' + esc(n.uploaded_name) + ')' : ''}: ${what}`
          : `Built from EMA: ${what}${n.author?' by '+esc(n.author):''}`) + (how ? ` (${esc(how)})` : '') + '.'
        + (n.pick_only ? ' <span class="sev-medium">Check first that it is this visit\'s note.</span>' : '')
        + ((r.others||[]).length ? ` The patient's other documents:${notePickList(r.others, 'Use this one instead', {who: false})}` : '');
      bindPicks(msg);
    } else if(r.status === 'ambiguous'){
      const cands = r.candidates || [];
      out.innerHTML = `<div class="small sev-medium">${esc(r.reason || 'A person has to choose the note.')}</div>${cands.length ? notePickList(cands, 'Use this note') : ''}`;
      bindPicks(out);
    } else {
      out.innerHTML = `<div class="small sev-medium">${esc(r.reason || 'No note was found in EMA.')}${r.source === 'upload' ? '' : ' Attach it above instead.'}</div>`;
    }
  } catch(e){ out.innerHTML = `<div class="sev-high small">Could not get the note: ${esc(e.message)}</div>`; }
  if(btn) btn.disabled = false;
}
// the appeal step's note check, opened from an earlier packet or from the route card's packet buttons
function xpShowHold(d, pk){
  const out = d && d.querySelector('#noteOut'); if(!out || !pk) return;
  const fold = out.closest('details'); if(fold) fold.open = true;
  showPacket(out, pk);
  out.scrollIntoView({block: 'nearest'});
}
function bindDrawerExtras(id, d){
  const out = d.querySelector('#noteOut'); if(!out) return;
  out.querySelectorAll('[data-pk-hold]').forEach(b => b.onclick = () =>
    xpShowHold(d, ((state.detailR || {}).packets || []).find(q => q.packet_id === b.dataset.pkHold)));
  const em = d.querySelector('#noteEma'); if(em) em.onclick = () => noteFromEma(id, d);
  const go = d.querySelector('#noteGo');               // one packet per click: a second click waits for the first answer
  go.onclick = async () => {
    if(go.disabled) return;
    const file = d.querySelector('#noteFile').files[0], text = d.querySelector('#noteText').value;
    if(!file && !text.trim()){ out.innerHTML = '<div class="sev-medium small">Attach the note or paste its text first.</div>'; return; }
    const fd = new FormData(); fd.append('finding_id', id); fd.append('text', text); fd.append('use_ai', d.querySelector('#noteAi').checked ? '1' : '0'); if(file) fd.append('file', file);
    go.disabled = true;
    out.innerHTML = '<div class="muted small">Reading the note and building the packet. A review takes about a minute.</div>';
    try {
      const p = await api('/api/appeals/build', {method:'POST', body: fd});
      showPacket(out, p);
      xpPacketBuilt(id, d, p);
    } catch(e){ out.innerHTML = `<div class="sev-high small">Could not build the packet: ${esc(e.message)}</div>`; }
    go.disabled = false;
  };
}
// The amount a person types for Paid, read the way a remittance or EMA prints it: 1250, 1,250, 1,250.00, $1,250.00, 1250.5.
// {cents} or {why}: anything else (letters, a minus sign, more than two decimals, 1e9, more than ten times the line's charge
// or more than $1,000,000) is asked again with the reason. zeroOk: the item's work brings no new money (paidZeroOk), so 0 is
// the answer there; anywhere else $0 is no Paid.
const PAID_EMPTY = 'Type the amount the payer paid, from the remittance or EMA. If you do not know it yet, leave the item as it is.';
const PAID_ZERO = 'Paid needs an amount above $0. If the payer paid nothing, mark it Lost or Let go instead.';
const PAID_TOP = 100000000;
function paidCents(typed, charge, zeroOk){
  const t = String(typed == null ? '' : typed).trim();
  if(!t) return {why: PAID_EMPTY};
  if(/^-|^\$\s*-/.test(t)) return {why: 'The amount paid cannot be below zero.'};
  const m = /^\$?\s*(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?$/.exec(t);
  if(!m) return {why: 'That is not an amount. Type it as the remittance or EMA shows it, for example 1,250.00.'};
  if(m[2] && m[2].length > 2) return {why: 'Use at most two decimals (dollars and cents).'};
  const cents = Number(m[1].replace(/,/g, '')) * 100 + (m[2] ? Number((m[2] + '0').slice(0, 2)) : 0);
  if(cents === 0) return zeroOk ? {cents: 0} : {why: PAID_ZERO};       // nothing recovered is a Paid only where nothing new was owed
  const top = charge > 0 ? Math.min(PAID_TOP, 10 * charge) : PAID_TOP;
  if(!Number.isSafeInteger(cents) || cents > top) return {why: top < PAID_TOP ? `That is more than ten times the charge on this line (${money2(top / 10)}). Check the amount.` : 'That is more than $1,000,000. Check the amount.'};
  return {cents};
}
// The items whose Paid can carry $0.00, as the site decides it (api._zero_paid_ok, review people-3): a payment EMA never
// posted, a credit that is only a takeback EMA never posted, a line the run found paid before the item was listed (its next
// step says to close it as Paid with $0.00 recovered), or a line whose state shows a payment that settles it. A payment
// that came after the item was listed is typed: it is what was recovered.
const PAID_ZERO_BLOCKS = new Set(['post', 'credit', 'paid']), PAID_ZERO_RULES = new Set(['post_payment', 'credit_unposted_takeback']);
const PAID_RECOVERED = new Set(['paid_later_ema', 'paid_later_835', 'rebilled_paid', 'forwarded_paid', 'second_payer_paid', 'claim_paid_in_ema']);
function paidZeroOk(f, x){
  f = f || {};
  if(PAID_ZERO_BLOCKS.has(f.appeal_block) || PAID_ZERO_RULES.has(f.state_rule)) return true;
  if(f.signal === 'REMIT_NOT_POSTED' && f.appeal_block !== 'call' && !((f.evidence || {}).informational)) return true;
  const proof = (((x || {}).explain || {}).state || {}).proof;
  return !!(proof && proof.rule && proof.rule !== 'moved_to_patient_open' && !(PAID_RECOVERED.has(proof.rule) && (proof.cents || 0) > 0));
}
// A packet built in the panel (attached, pasted or from EMA) can be read and faxed at once: it joins the item's packets
// (the fax buttons read them when pressed) and the next step's box is drawn again, so its main button moves on to the fax
function xpPacketBuilt(id, d, p){
  const r = state.detailR;
  if(!p || !p.packet_id || !r || state.detailId !== id) return;
  r.packets = [{packet_id: p.packet_id, created_at: p.created_at || new Date().toISOString(), note_name: p.note_name || '', note_hold: p.note_hold || null}]
    .concat((r.packets || []).filter(q => q.packet_id !== p.packet_id));
  if(typeof xpRedrawBox === 'function') xpRedrawBox(id, d);
}
// ---------------------------------------------------------------- an item's verdict, next step and story (remitwatch.story)
// Asked for beside the item itself (/api/findings/{id}/explain), so the panel opens at once. What happened and how sure the
// site is, then what to do next with its one main button (the rest under More), then the evidence row by row with the
// balance line, folded until asked for. The same words for the states as everywhere else.
const XP_STATE = {work_now:['Work now','xs-now'], waiting:['Waiting','xs-wait'], done:['Done','xs-done'], accepted:['Accepted','xs-quiet'],
  expired:['Expired','xs-quiet'], cant_check:["Can't be checked",'xs-warn']};
const XP_STRENGTH = {proven:'Proven: two sources agree', one_source:'From one source', inferred:'Inferred'};
const XP_SOURCE = {'835':'835', '837':'837 claim file', production:'EMA posting', ar:'EMA A/R', weekly:'EMA export', claim_status:'Claim status', remitwatch:'RemitWatch'};
const XP_BUTTON = {build_packet:'Build the appeal packet', attach_note:'Attach the visit note', find_note:'Find the note in EMA', upload_note:'Upload the note',
  fax_payer:'Fax it to the payer', copy_instructions:'Copy the steps', mark_sent:'Mark sent', log_outcome:'Log what happened', mark_posted:'Mark posted in EMA',
  mark_billed:'Mark billed', accept_pattern:'Accept this pattern', let_go:'Let go', reopen:'Reopen', request_era:'Copy the ERA request', open_in_ema:'Open in EMA',
  billed_patient:'Billed the patient', mark_done:'Done'};
// the one button that moves the item on, the first of these its next step offers (fax only once a packet can be read first)
const XP_FIRST = ['fax_payer','build_packet','mark_posted','billed_patient','mark_billed','mark_sent','log_outcome','copy_instructions','request_era','reopen'];
const XP_OUTCOME = {auto_written_off:'Auto write-off', written_off_while_pending:'Written off while pending', lost_to_timely_filing:'Lost to late filing'};
// what stops an appeal outright (the server holds to it): a line paid, credited, reprocessed or waiting on the payer. A line
// EMA shows settled, or the archive shows likely paid, is checked first: its fax stays under More and asks for the check
const XP_NO_FAX = new Set(['paid', 'credit', 'reprocessed', 'waiting']);
const XP_CHECK_FIRST = new Set(['settled_in_ema', 'likely_paid']);
XP_BUTTON.decide = 'Decide what to do';

function xpPrimary(nx, hasPacket){
  const b = xpButtons(nx);
  if(b.includes('fax_payer') && hasPacket && !XP_CHECK_FIRST.has(nx.appeal_block)) return 'fax_payer';
  // a payer's published rule that never pays the code leads: no appeal, and nothing to send first; the steps say what to check
  if(nx.payer_rule === 'never_payable' && nx.appeal_ok === false) return b.includes('copy_instructions') ? 'copy_instructions' : null;
  // a correction made in EMA: read the steps, do it there, then mark it sent
  if(['corrected','eob_first'].includes(nx.route) && b.includes('mark_sent')) return 'mark_sent';
  // a decision first (fix, appeal or let go): the three choices, never "what did the payer do"
  if(nx.route === 'decide') return 'decide';
  return XP_FIRST.find(k => k !== 'fax_payer' && b.includes(k)) || null;
}
// the next step's buttons the page offers: never a fax for a line the server would not send (XP_NO_FAX)
function xpButtons(nx){
  const b = (nx && nx.buttons) || [];
  return nx && nx.appeal_ok === false && XP_NO_FAX.has(nx.appeal_block) ? b.filter(k => k !== 'fax_payer') : b;
}
function xpChips(f, ws){
  const st = XP_STATE[ws];
  return (st ? `<span class="xs ${st[1]}">${st[0]}</span>` : '') + (XP_OUTCOME[f.ema_outcome] ? ` <span class="xs xs-warn">${XP_OUTCOME[f.ema_outcome]}</span>` : '');
}
// The state a person's status gives the item since the newest run (the finding says so: state_by_status), when it differs
// from the state the run stored that the story explains: the verdict and the next step follow the person, until the next
// run works them out again. Otherwise the explain answer as it is.
const XP_BY_STATUS = {waiting: ["Wait for the payer's answer. The next run dates the follow-up.", ['log_outcome', 'reopen']],
  accepted: ['Nothing to do. Reopen it to work it again.', ['reopen']], done: ['Nothing to do.', ['reopen']],
  work_now: ['Work it as its reason says; the next run gives its exact next step.', ['mark_sent', 'log_outcome', 'let_go']]};
function xpEffective(f, x){
  const ex = (x && x.explain) || {}, told = (ex.verdict || {}).state || (ex.state || {}).work_state;
  // a state the server worked out from the person's own newer action (Billed the patient, Done: by_person) is newer than
  // any status words here, and says whom the item waits on (the patient, never the payer)
  if(!f.state_by_status || !f.work_state || f.work_state === told || !XP_BY_STATUS[f.work_state] || (ex.state || {}).by_person) return x;
  const when = f.status_changed_at ? ` on ${longDate(practiceDate(f.status_changed_at))}` : '';
  const [action, buttons] = XP_BY_STATUS[f.work_state];
  return Object.assign({}, x, {explain: Object.assign({}, ex, {
    verdict: {state: f.work_state, sentence: `${(typeof STATUS_WORDS !== 'undefined' && STATUS_WORDS[f.status]) || f.status} by ${f.worked_by && f.worked_to === f.status ? f.worked_by : 'a person'}${when}.`, rows: []},
    next_step: {route: 'status', action, buttons}, state: {work_state: f.work_state}})});
}
// EMA's address for the item's claim (Open in EMA): the next step's own, or the template EMA's block gives with the item's
// EMA claim number (6 to 12 digits, as the Data tab builds it); none when either is missing
function xpEmaUrl(f, x, nx){
  if(nx && nx.claim_url) return nx.claim_url;
  const b = (x && x.ema_block) || {}, tpl = String(b.claim_url || ''), n = String(f.ema_link != null ? f.ema_link : ((b.links || [])[0] || ''));
  return tpl.startsWith('https://') && tpl.split('{claim}').length === 2 && /^\d{6,12}$/.test(n) ? tpl.replace('{claim}', n) : null;
}
// the verdict's own "(row 2)" and "(rows 1 and 3)" go when the Rows link beside it says the same
const xpUncited = s => String(s || '').replace(/\s*\(rows? \d+(?:(?:, | and |-)\d+)*\)/gi, '');
// what to do and how sure: the next step (who does it and by when), its one main button, the rest under More; then Why, the
// verdict in one line with its proof and its basis on request (what it is worth and how sure the site is). The state, the
// item's place and Previous and Next are in the panel's head (panelHeadHtml). Where the route the payer takes leads (the
// item is ready to go), the route's own button is the one filled button and the next step's turns secondary.
function xpBoxHtml(f, x, r){
  x = xpEffective(f, x);
  const ex = x.explain || {}, vd = ex.verdict || {}, nx = ex.next_step || {}, st = ex.state || {};
  const ws = vd.state || st.work_state || f.work_state;
  const follow = ws === 'waiting' && (vd.follow_up_on || st.follow_up_on || f.follow_up_on);
  const rowsCited = (vd.rows || []).filter(n => n != null);
  const strength = vd.strength ? `<span${vd.strength === 'inferred' && vd.rule ? ` title="${esc('Rule: ' + vd.rule)}"` : ''}>${esc(XP_STRENGTH[vd.strength] || vd.strength)}</span>` : '';
  const legal = st.legal_deadline || f.legal_deadline;
  const fu = follow ? panelDue(f, x) : null;
  const due = fu ? `Follow up <b>${esc(longDate(follow))}</b> <span class="${fu.now ? 'due-now' : ''}">(${esc(fu.words)})</span>`
    : nx.deadline ? `By <b${nx.basis ? ` title="${esc(nx.basis)}"` : ''}>${esc(longDate(nx.deadline))}</b> <span class="${closesText(nx.deadline).cls}">(${esc(closesText(nx.deadline).text)})</span>${legal && legal !== nx.deadline ? ` · Payer's limit: <b>${esc(longDate(legal))}</b>` : ''}` : '';
  const rec = nx.recovery || {};
  const worth = nx.expected_cents ? `At stake <b>${money2(nx.expected_cents)}</b>${nx.ranked_cents != null ? ` · ranked by about <b>${money2(nx.ranked_cents)}</b> expected back` : ''}` : '';
  const hasPacket = (r.packets || []).length > 0;
  const primary = xpPrimary(nx, hasPacket);
  const url = xpEmaUrl(f, x, nx);
  const more = xpButtons(nx).concat(url && !(nx.buttons || []).includes('open_in_ema') ? ['open_in_ema'] : []).filter(k => k !== primary && XP_BUTTON[k]);
  const steps = (nx.steps || []).filter(Boolean);
  const sib = (nx.siblings || []).length;
  const card = xpRouteShown(f, x);
  // the route leads once nothing is left to prepare: its button (call, open the portal, start the fax) is the one filled
  // button; while a packet or a note is still to be made, or a decision taken, the next step's own button leads
  const leads = !!card && !card.fallback && (hasPacket || !['build_packet', 'attach_note', 'find_note', 'upload_note', 'decide'].includes(primary));
  // the route is the current step when it leads (above the buttons, its own button filled); while the next step's own button
  // leads (a packet to build, a decision to take), the route is the step after it and comes below the buttons
  const route = xpRouteHtml(f, x, r, !leads);
  const key = k => ({mark_sent: 's', let_go: 'l', mark_billed: 'b', billed_patient: 'b', log_outcome: 'n', open_in_ema: 'e'})[k];
  return `<div class="todo-box xp-box next" id="xpBox">
    <div class="next-label">Next step${nx.who ? ` <span>· ${esc(nx.who)}</span>` : ''}</div>
    ${nx.action ? `<p class="next-text todo-next">${esc(nx.action)}</p>` : ''}
    ${due ? `<div class="small next-due">${due}</div>` : ''}
    ${steps.length ? `<details class="xp-steps"><summary class="small">${steps.length === 1 ? 'The step' : `The ${steps.length} steps`}</summary><ol class="small">${steps.map(s => `<li>${esc(s)}</li>`).join('')}</ol></details>` : ''}
    ${leads ? route : ''}
    ${(nx.needs || []).length ? `<div class="small muted">It needs: ${esc(nx.needs.join('; '))}</div>` : ''}
    ${sib ? `<div class="small muted">${pl(sib, 'other open item')} on this visit: work them together.</div>` : ''}
    <div class="todo-err sev-high small" id="xpErr" role="alert"></div>
    <div class="actions todo-actions next-actions">${primary ? `<button${leads ? '' : ' class="primary"'} data-xp="${primary}" title="${esc(XP_BUTTON[primary])} (Enter)" id="xpMain">${esc(XP_BUTTON[primary])}</button>` : ''}
      ${more.length ? `<details class="xp-more"><summary class="small">More</summary><div class="actions">${more.map(k => `<button class="ghost" data-xp="${k}"${key(k) ? ` title="Key ${key(k)}"` : ''}>${esc(XP_BUTTON[k])}</button>`).join('')}</div></details>` : ''}</div>
    <div id="xpMsg" class="small"></div>${leads ? '' : route}</div>
    ${vd.sentence || strength || worth ? `<details class="why" id="xpWhyLine"><summary><span class="why-label">Why</span> <span class="xp-verdict">${esc(rowsCited.length ? xpUncited(vd.sentence) : (vd.sentence || 'How sure the site is'))}</span></summary>
      <div>${rowsCited.length ? `<a href="#" class="xp-rows small" data-rows="${rowsCited.join(',')}">${rowsCited.length > 1 ? 'Rows' : 'Row'} ${rowsCited.join(', ')}</a> in the story below. ` : ''}${strength}
      ${worth ? `<div class="small"${rec.basis ? ` title="${esc(rec.basis)}"` : ''}>${worth}</div>` : ''}</div></details>` : ''}`;
}
// ---------------------------------------------------------------- how it reaches the payer (remitwatch.routes)
// The route card at the top of the next step: the easiest way the payer accepts as one line with its main action (open the
// portal, call, start the fax only to a number a person confirmed, print the letter), the deadline, the fields filled from
// the store each with its copy button, Copy all (the c key), the attachments with what the app builds, the other ways the
// payer takes folded, the ways it refuses, the source line, and I sent it (the existing Sent, with the channel and an
// optional reference number). A field the store cannot fill says what goes there; nothing is made up.
const RC_CH = {payer_portal: 'Portal', clearinghouse: 'Clearinghouse', phone: 'Phone', fax: 'Fax', mail: 'Mail', email: 'Email'};
// the card follows the run's next step: once a person's newer status changed what the item waits on, it steps aside
function xpRouteShown(f, x){
  const c = x && x.route_card; if(!c) return null;
  const nx = ((xpEffective(f, x) || {}).explain || {}).next_step || {};
  return nx.route === 'status' && f.work_state !== 'work_now' ? null : c;
}
// a field filled in part (the name and address, no email on file) shows what it still needs beside its value (review r2-8).
// keyed: the route's own fields, where the first nine filled ones take the keys 1 to 9 (shown on the row) to copy them
function rcFieldRows(list, at, keyed){
  return (list || []).map(v => { const i = at.push(v) - 1, k = keyed && v.value ? at.filter(o => o.value).length : 0, key = k && k <= 9 ? k : 0;
    const need = v.value && v.fill ? `<div class="muted small">also needed: ${esc(v.fill)}</div>` : '';
    return `<tr class="copyrow${v.value ? '' : ' rc-empty'}"><td class="rc-key">${key ? `<kbd>${key}</kbd>` : ''}</td><td class="small">${esc(v.label)}</td><td>${v.value ? `<b>${esc(v.value)}</b>${need}` : `<span class="muted small">${esc(v.set ? 'as the label says' : (v.fill ? 'fill: ' + v.fill : ''))}</span>`}</td>
      <td>${v.value ? `<button class="ghost small copy-btn" data-rc-copy="${i}"${key ? ` data-rc-key="${key}"` : ''} aria-label="Copy ${esc(v.label)}">Copy</button>` : ''}</td></tr>`; }).join('');
}
// the requests whose mail way is the app's own letter or packet (the panel's Appeal letter); any other mail way prints a cover
// sheet of the card's filled fields to send with it (a refund check, a corrected claim on paper: review routes-r2-11)
const RC_LETTER = new Set(['reconsideration_or_first_appeal', 'medicare_redetermination', 'second_level_appeal', 'timely_filing_proof']);
function rcAction(f, b, r, nx, c){
  const a = b.action || {};
  if(b.channel === 'payer_portal' && a.url) return `<button class="primary" data-rc="open" data-url="${esc(a.url)}">${esc(a.label)}</button>`;
  if(b.channel === 'phone' && a.tel) return `<button class="primary" data-rc="call" data-tel="${esc(a.tel)}">${esc(a.label)}</button>`;
  if(b.channel === 'fax'){
    // the app's own send carries a first-level appeal to the confirmed appeal number: Start the fax only where that number is
    // the one the payer publishes for this request (sendable); a request the send does not carry is faxed by hand (r1-4)
    const fx = b.fax || {};
    if(fx.sendable && (nx.buttons || []).includes('fax_payer')) return `<button class="primary" data-rc="fax">Start the fax</button>`;
    if(fx.by_hand || fx.sendable) return fx.published ? `<button class="ghost" data-rc="copy_fax">Copy the fax number</button>` : '';
    return `<button class="ghost" data-rc="faxes">Confirm the number first</button>`;
  }
  if(b.channel === 'mail'){
    const letter = RC_LETTER.has((c || {}).request) && ((r.packets || []).length || f.claim_key || ['TAKEBACK','FEE_SKIM'].includes(f.signal));
    const held = ((r.packets || [])[0] || {}).note_hold;
    if(letter && held) return `<button class="primary" data-rc="checknote">Check the note first</button>`;
    return letter ? `<button class="primary" data-rc="letter">${(r.packets || []).length ? 'Print the packet' : 'Print the letter'}</button>`
      : `<button class="primary" data-rc="sheet">Print the cover sheet</button>`;
  }
  if(b.channel === 'email' && b.where) return `<button class="ghost" data-rc="copy_where">Copy the address</button>`;
  // a clearinghouse way is sent or checked from EMA: its button opens the item's claim there (review routes-r2-13)
  if(b.channel === 'clearinghouse' && a.label) return `<button class="primary" data-rc="ema">${esc(a.label)}</button>`;
  return '';
}
// the cover sheet a mail way with no letter of the app's own goes out with: the clean block Copy all copies (where to send
// it, every filled field) and what to enclose, in a page of its own to print (review routes-r2-11)
function rcPrintSheet(c, b){
  const lines = String(c.copy_all || '').split('\n').filter(Boolean), head = lines.shift() || 'Cover sheet';
  const rows = lines.map(l => { const i = l.indexOf(': ');
    return i > 0 ? `<tr><th>${esc(l.slice(0, i))}</th><td>${esc(l.slice(i + 2))}</td></tr>` : `<tr><td colspan="2">${esc(l)}</td></tr>`; }).join('');
  const att = ((b || {}).attachments || []).map(a => `<li>${esc(a.label)}</li>`).join('');
  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>${esc(head)}</title><style>body{font:14px/1.5 system-ui,sans-serif;margin:32px;color:#111;background:#fff}
    th{text-align:left;padding:4px 16px 4px 0;vertical-align:top}td{padding:4px 0}button{margin-bottom:16px}h1{font-size:18px}@media print{button{display:none}}</style></head>
    <body><button onclick="print()">Print</button><h1>${esc(head)}</h1><table>${rows}</table>
    ${att ? `<p><b>Enclose</b></p><ul>${att}</ul>` : ''}</body></html>`;
  const url = URL.createObjectURL(new Blob([html], {type: 'text/html'}));
  window.open(url, '_blank', 'noopener,noreferrer');
  setTimeout(() => URL.revokeObjectURL(url), 120000);
}
const rcChip = (v, why) => v === false ? ` <span class="chip hint" title="${esc(why || '')}">not confirmed</span>` : '';
function rcAttach(list, r, f){
  const out = (list || []).map(a => {
    const k = a.kind, pk = (r.packets || [])[0];
    const btn = k === 'letter' ? '<button class="ghost small" data-rc="letter">Open the letter</button>'
      : k === 'packet' ? (pk ? (pk.note_hold ? '<button class="ghost small" data-rc="checknote">Check the note first</button>'
          : `<a class="small" href="/api/packets/${encodeURIComponent(pk.packet_id)}/pdf" target="_blank" rel="noopener">Open the packet</a>`)
        : '<button class="ghost small" data-rc="packet">Build the packet</button>')
      : k === 'remittance' && r.bill ? '<button class="ghost small" data-rc="claim">See the remittance</button>' : '';
    return `<li>${esc(a.label)} ${btn}</li>`; }).join('');
  return out ? `<div class="small"><b>Attach</b><ul class="rc-attach">${out}</ul></div>` : '';
}
function rcOther(o, at){
  const rows = rcFieldRows((o.fields || []).filter(v => v.value), at);
  return `<li><b>${esc(RC_CH[o.channel] || o.channel)}:</b> ${esc(o.line)}${o.where && !(o.action || {}).url ? ` · ${esc(o.where)}` : ''}${(o.action || {}).url ? ` · <a href="${esc(o.action.url)}" target="_blank" rel="noopener noreferrer">open</a>` : ''}
    ${o.verified ? '' : ` <span class="chip hint" title="${esc(o.unverified_why || '')}">not confirmed</span>`}
    ${o.fax ? `<div class="muted">${esc(o.fax.words)}</div>` : ''}${o.notes ? `<div class="muted">${esc(o.notes)}</div>` : ''}
    ${rows ? `<table class="small rc-fields"><tbody>${rows}</tbody></table>` : ''}</li>`;
}
// the sent words: what "I sent it" records, so a later "no" is read for what was sent (routes.sent_request); a status
// request records "I asked the payer" as a line in the item's history and changes no status (review routes-r1-2)
const rcSentHtml = (c, how) => `<div class="rc-sent after-send">${how ? '<label class="small">How you sent it <input id="rcHow" maxlength="80" autocomplete="off" placeholder="the payer\'s portal, fax or mail"></label>' : ''}
      <label class="small">Reference number (optional) <input id="rcRef" maxlength="60" autocomplete="off" placeholder="the portal's submission ID"></label>
      <button id="rcSent">${c.request === 'claim_status_or_reprocess' ? 'I asked the payer' : 'I sent it'}</button></div>`;
const rcNamesOff = c => c.names_shown ? '' : '<div class="small muted">Names are switched off on this site: the patient\'s name and member ID are left for you to fill from EMA.</div>';
// quiet: the next step's own button leads (a packet still to build, a call to log), so the route's button is not filled
function xpRouteHtml(f, x, r, quiet){
  const err = x && x.route_card_error;
  const c = xpRouteShown(f, x);
  if(!c) return err ? `<div class="small muted">${esc(err)}</div>` : '';
  if(c.fallback && c.single_level) return `<section class="rc-card" id="rcCard" aria-label="How to send it"><div class="small"><b>How to send it:</b> ${esc(c.fallback)}</div>
    ${c.source_line ? `<div class="small muted rc-source">${esc(c.source_line)}</div>` : ''}</section>`;
  if(c.fallback){
    // a payer the table does not cover: the claim's own facts to copy, Copy all, and I sent it with how it went (r2-12)
    const at = []; xpRouteHtml.vals = at;
    const facts = rcFieldRows(c.facts, at, true);
    return `<section class="rc-card" id="rcCard" aria-label="How to send it"><div class="small"><b>How to send it:</b> ${esc(c.fallback)}</div>
    <div class="actions rc-actions"><button class="ghost" id="rcCopyAll" title="Copy every filled field (c)">Copy all</button><span class="small muted rc-count" id="rcCount" aria-live="polite"></span></div>
    ${facts ? `<table class="small rc-fields"><tbody>${facts}</tbody></table>` : ''}
    ${rcNamesOff(c)}
    ${rcSentHtml(c, true)}</section>`;
  }
  const nx = ((x || {}).explain || {}).next_step || {}, b = c.best, at = [];
  xpRouteHtml.vals = at;
  const due = c.deadline ? ` · by <b${c.deadline_basis ? ` title="${esc(c.deadline_basis)}"` : ''}>${esc(longDate(c.deadline))}</b> <span class="${closesText(c.deadline).cls}">(${esc(closesText(c.deadline).text)})</span>` : '';
  const facts = rcFieldRows(b.fields, at, true) + rcFieldRows(b.facts, at, true);
  const others = (c.others || []).map(o => rcOther(o, at)).join('');
  // the Also fold says what the card's own line says: whether the way was confirmed, the fax words, the limit (r1-5)
  const ab = (c.also || {}).best || {};
  const also = c.also ? `<details class="dfold rc-also"><summary>Also: ${esc(c.also.request_label)} (${esc(c.also.why || '')})</summary>
      <div class="small"><b>${esc(RC_CH[ab.channel] || '')}:</b> ${esc(ab.line)}${ab.where && !(ab.action || {}).url ? ' · ' + esc(ab.where) : ''}${rcChip(ab.verified, ab.unverified_why)}</div>
      ${ab.fax ? `<div class="small ${ab.fax.sendable ? '' : 'sev-medium'}">${esc(ab.fax.words)}</div>` : ''}
      ${(ab.attachments || []).length ? `<div class="small muted">With: ${esc(ab.attachments.map(a => a.label).join('; '))}</div>` : ''}
      ${c.also.time_limit ? `<div class="small muted">Limit: ${esc(c.also.time_limit)}${c.also.time_limit_verified ? '' : ' (not confirmed)'}</div>` : ''}</details>` : '';
  return `<section class="rc-card send-step" id="rcCard" aria-label="How to send it">
    <div class="rc-head"><span class="chip">${esc(RC_CH[b.channel] || b.channel)}</span> <b>${esc(b.line)}</b> <span class="easy">Easiest</span>${due}
      ${b.required ? ' <span class="chip">required</span>' : ''}${rcChip(b.verified, b.unverified_why)}</div>
    <div class="actions rc-actions">${quiet ? rcAction(f, b, r, nx, c).replace(/class="primary" /g, '') : rcAction(f, b, r, nx, c)}<button class="ghost" id="rcCopyAll" title="Copy every filled field (c)">Copy all</button><span class="small muted rc-count" id="rcCount" aria-live="polite"></span></div>
    <div class="small muted">${esc(c.payer)}: ${esc(c.request_label)}${c.variant_why ? ' · ' + esc(c.variant_why) : ''}</div>
    ${c.deadline_note ? `<div class="small sev-medium">${esc(c.deadline_note)}</div>` : ''}
    ${b.where && !(b.action || {}).url ? `<div class="small">Send to: <b>${esc(b.where)}</b>${b.form ? ' · form: ' + esc(b.form) : ''}</div>` : (b.form ? `<div class="small">Form: ${esc(b.form)}</div>` : '')}
    ${b.fax ? `<div class="small ${b.fax.sendable ? '' : 'sev-medium'}">${esc(b.fax.words)}</div>` : ''}
    ${facts ? `<table class="small rc-fields"><tbody>${facts}</tbody></table>` : ''}
    ${facts ? '<div class="privacy-note">Copied details stay on this computer\'s clipboard until you copy something else. RemitWatch keeps no record of what you copied.</div>' : ''}
    ${rcNamesOff(c)}
    ${(b.steps || []).length ? `<details class="xp-steps"><summary class="small">Where to click (${b.steps.length})</summary><ol class="small">${b.steps.map(s => `<li>${esc(s)}</li>`).join('')}</ol></details>` : ''}
    ${rcAttach(b.attachments, r, f)}
    ${b.notes ? `<div class="small muted">${esc(b.notes)}</div>` : ''}
    ${(c.not_accepted || []).map(n => `<div class="small sev-medium">Not accepted: ${esc(n.name)}${n.notes ? ': ' + esc(n.notes) : ''}</div>`).join('')}
    ${others ? `<details class="dfold rc-others"><summary>Other ways ${esc(c.payer)} takes it (${c.others.length})</summary><ul class="small">${others}</ul></details>` : ''}
    ${(c.alternatives || []).length ? `<details class="dfold"><summary>Another plan type?</summary><ul class="small">${c.alternatives.map(a => `<li>${esc(a.when)}: ${esc(a.best)}${rcChip(a.verified, a.unverified_why)}</li>`).join('')}</ul></details>` : ''}
    ${also}
    <div class="small muted rc-source">${b.sources && b.sources[0] && /^https:\/\//.test(b.sources[0].url) ? `<a href="${esc(b.sources[0].url)}" target="_blank" rel="noopener noreferrer">${esc(b.source_line)}</a>` : esc(b.source_line)}${c.time_limit ? ` · Limit: ${esc(c.time_limit)}${c.time_limit_verified ? '' : ' (not confirmed)'}` : ''}</div>
    ${rcSentHtml(c, false)}
  </section>`;
}
// the card's buttons: a copy, a page or a window to open, the fax only through the app's own send (its confirmed number),
// and I sent it as the existing Sent with what was sent, the channel and the reference in its note; I asked the payer as a
// line in the item's history that changes no status (review routes-r1-2)
function xpRouteBind(f, x, r, d, h){
  const c = xpRouteShown(f, x), box = d.querySelector('#rcCard');
  if(!c || !box || c.single_level) return;
  const vals = xpRouteHtml.vals || [], b = c.best || {};
  const nx = ((xpEffective(f, x) || {}).explain || {}).next_step || {};
  // a copied field gets a check, the next field to paste is marked, and the count says how many are copied
  const own = [...box.querySelectorAll(':scope > table.rc-fields [data-rc-copy]')], done = new Set(), count = box.querySelector('#rcCount');
  const mark = bt => { if(!own.includes(bt)) return; done.add(bt); const tr = bt.closest('tr'); if(tr) tr.classList.add('copied'); bt.textContent = 'Copied';
    own.forEach(o => { const t = o.closest('tr'); if(t) t.classList.remove('is-next'); });
    const nx2 = own.find(o => !done.has(o)), t2 = nx2 && nx2.closest('tr'); if(t2) t2.classList.add('is-next');
    if(count) count.textContent = `${done.size} of ${own.length} copied`; };
  if(own[0] && own[0].closest('tr')) own[0].closest('tr').classList.add('is-next');
  if(count && own.length) count.textContent = `${own.length} ${own.length === 1 ? 'field' : 'fields'} to copy`;
  box.querySelectorAll('[data-rc-copy]').forEach(bt => bt.onclick = async () => { const v = vals[Number(bt.dataset.rcCopy)]; if(v && await h.copy(v.value, v.label)) mark(bt); });
  const all = box.querySelector('#rcCopyAll'); if(all) all.onclick = () => h.copy(c.copy_all || '', 'Every filled field');
  box.querySelectorAll('[data-rc]').forEach(bt => bt.onclick = async () => {
    const k = bt.dataset.rc;
    if(k === 'open'){ window.open(bt.dataset.url, '_blank', 'noopener,noreferrer'); return; }
    if(k === 'call'){ location.href = 'tel:' + bt.dataset.tel; return; }
    if(k === 'fax'){ h.act.fax_payer(); return; }
    if(k === 'faxes'){ if(typeof closeDetail === 'function') closeDetail(); state.tab = 'faxes'; location.hash = 'faxes'; render(); return; }
    if(k === 'checknote'){ xpShowHold(d, (r.packets || [])[0]); return; }
    if(k === 'letter'){ const pk = (r.packets || [])[0];
      if(pk && pk.note_hold){ xpShowHold(d, pk); return; }
      if(pk && bt.textContent.includes('packet')) window.open('/api/packets/' + encodeURIComponent(pk.packet_id) + '/pdf', '_blank', 'noopener');
      else { const l = d.querySelector('#btnAppeal'); if(l) l.click(); else rcPrintSheet(c, b); } return; }
    if(k === 'sheet'){ rcPrintSheet(c, b); return; }
    if(k === 'ema'){ const url = xpEmaUrl(f, x, nx);
      if(url) window.open(url, '_blank', 'noopener,noreferrer');
      else h.err('Open this claim in EMA and send it from there: no EMA address is set for this site.'); return; }
    if(k === 'copy_fax'){ h.copy((b.fax || {}).published || '', 'The fax number'); return; }
    if(k === 'packet'){ h.act.build_packet(); return; }
    if(k === 'claim'){ const oc = d.querySelector('#openClaim'); if(oc) oc.click(); return; }
    if(k === 'copy_where'){ h.copy(b.where, 'The address'); return; }
  });
  const sent = box.querySelector('#rcSent');
  if(sent) sent.onclick = async () => {
    if(sent.disabled || xpWalkGuarded()) return;
    const ref = (box.querySelector('#rcRef').value || '').trim().slice(0, 60);
    const howBox = box.querySelector('#rcHow');
    const how = howBox ? (howBox.value || '').trim().slice(0, 80) : `${RC_CH[b.channel] || b.channel}: ${b.name}`;
    if(howBox && !how){ h.err('Say how you sent it (the portal, a fax, the mail): it is kept in its history.'); return; }
    sent.disabled = true;
    try {
      if(c.request === 'claim_status_or_reprocess'){
        // a status call is no Sent: the claim no payer answered stays to do until its first-filing limit, and what the
        // payer said is logged with Log what happened
        await h.event('asked', `${how}${ref ? '. Reference ' + ref : ''}`);
        h.say('Noted in its history. When the payer answers, log what it said with Log what happened.');
      } else {
        await h.status('appealed', `Sent ${c.sent_words || 'it'} by ${how}${ref ? '. Reference ' + ref : ''}`);
      }
    }
    catch(e){ h.err('Not saved: ' + (e.message || 'no answer from the site') + '. Try again.'); }
    finally { sent.disabled = false; }
  };
}
// the evidence: every event on the line in date order, each with its source, the rows the verdict rests on marked, and the
// balance line (it reconciles to the cent, or says by how much it does not and why)
function xpStoryHtml(sto, decisive){
  const rows = (sto && sto.rows) || [], mark = new Set((decisive || []).map(Number));
  const bal = (sto && sto.balance) || {};
  const notes = ((sto && sto.notes) || []).filter(Boolean);
  if(!rows.length && !bal.text) return '';
  const head = `${pl(rows.length, 'event')}${bal.text ? (sto.reconciles ? ' · balances to the cent' : ` · ${money2(sto.gap_cents)} does not balance`) : ''}`;
  return `<details class="dfold" id="xpStory"><summary>The story: ${esc(head)}</summary>
    <div class="scroll"><table class="small xp-story"><thead><tr><th>#</th><th>Date</th><th>From</th><th>What happened</th><th class="num">Amount</th></tr></thead>
    <tbody>${rows.map(w => `<tr data-n="${esc(w.n)}" class="${w.decisive || mark.has(Number(w.n)) ? 'has-item' : ''}"><td>${esc(w.n)}</td><td class="day">${esc(w.date ? longDate(w.date) : '')}</td>
      <td><span class="chip">${esc(XP_SOURCE[w.source] || w.source)}</span></td>
      <td>${esc(w.text)}${(w.codes || []).length ? `<div class="muted">${w.codes.map(c => esc(c.code) + (c.meaning ? ': ' + esc(c.meaning) : '')).join(' · ')}</div>` : ''}</td>
      <td class="num">${w.amount_cents != null ? money2(w.amount_cents) : ''}</td></tr>`).join('')}</tbody></table></div>
    ${bal.text ? `<div class="small ${sto.reconciles ? '' : 'sev-medium'}" style="margin-top:6px"><b>Balance.</b> ${esc(bal.text)}</div>` : ''}
    ${notes.map(n => `<div class="note">${esc(n)}</div>`).join('')}</details>`;
}
// what EMA's reports hold on the claim (ema_checks.claim_block): the line as EMA has it, its A/R rows, and on the server
// every posting (on Vercel, a note that they are kept on the server)
function xpEmaHtml(b){
  if(!b || !((b.lines || []).length || (b.ar || []).length)) return '';
  const lines = (b.lines || []).map(l => `<tr><td>${esc(l.code)}</td><td class="day">${esc(l.service_date || '')}</td><td class="num">${money2(l.charges_cents)}</td><td class="num">${money2(l.ins_paid_cents)}</td>
    <td class="num">${money2((l.contractual_cents || 0) + (l.noncontractual_cents || 0))}</td><td class="num">${money2(l.balance_cents)}</td><td class="small muted">${esc(l.status || '')}${l.as_of ? ' · as of ' + esc(l.as_of) : ''}</td></tr>`).join('');
  const ar = (b.ar || []).map(a => `<tr><td>${esc(a.code || '')}</td><td class="day">${esc(a.service_date || '')}</td><td class="num">${money2(a.balance_cents)}</td><td class="num">${money2(a.denial_cents)}</td>
    <td class="small">${esc(a.status || '')}${a.claim_action ? ' · ' + esc(a.claim_action) : ''}${a.has_note ? ' · a note' : ''}</td><td class="small muted">${esc(a.as_of || '')}</td></tr>`).join('');
  return `<details class="dfold" id="xpEma"><summary>What EMA's reports show for this claim</summary>
    ${lines ? `<table class="small"><thead><tr><th>Code</th><th>Date</th><th class="num">Charge</th><th class="num">Insurance paid</th><th class="num">Written off</th><th class="num">Open</th><th>EMA status</th></tr></thead><tbody>${lines}</tbody></table>` : ''}
    ${ar ? `<h3 class="small">On the A/R</h3><table class="small"><thead><tr><th>Code</th><th>Date</th><th class="num">Balance</th><th class="num">Denied</th><th>Status</th><th>Pulled</th></tr></thead><tbody>${ar}</tbody></table>` : ''}
    ${(b.postings || []).length ? `<div class="small muted">${pl(b.postings.length, 'EMA posting')} on these lines are in the story above.</div>` : ''}
    ${b.postings_note ? `<div class="note">${esc(b.postings_note)}</div>` : ''}
    <div class="small muted">EMA's claim status is context only: what happened to this line is in the story.</div></details>`;
}
// "Why <payer> does not pay <code>" (remitwatch.rarely_paid, the owner's decision 4): what the newest run found in the
// remittances (lines answered and paid, the reasons, remarks, modifiers, places of service and codes billed with it), the
// payer's published rule with its sources (each fetched twice), what the note or the claim must show, and the verdict in
// plain words. Open when no note can change it; folded otherwise.
const XP_RULE_CLS = {never_payable: 'sev-high', claim: 'sev-medium', note: 'ok', unclear: 'muted'};
function xpRuleSources(list){
  const by = [];
  (list || []).forEach(s => { if(!s || !s.title) return; let g = by.find(x => x.title === s.title);
    if(!g){ g = {title: s.title, url: s.url, date: s.date, quotes: []}; by.push(g); } if(s.quote && g.quotes.length < 2) g.quotes.push(s.quote); });
  return by.map(g => `<div>${/^https:\/\//.test(String(g.url || '')) ? `<a href="${esc(g.url)}" target="_blank" rel="noopener noreferrer">${esc(g.title)}</a>` : esc(g.title)}${g.date ? ', ' + esc(g.date) : ''}${g.quotes.map(q => `<div class="muted"><i>“${esc(q)}”</i></div>`).join('')}</div>`).join('');
}
function xpPayerRuleHtml(blocks){
  // a rule shown in full once: a later code under the same rule (one state's enrollment rule for several codes of a
  // claim, say) points back to it instead of repeating it
  const told = [];
  const sameAs = b => told.find(t => t.payer_group === b.payer_group && t.verdict === b.verdict && t.rule === b.rule
    && (t.what_to_show || '') === (b.what_to_show || '') && (t.condition || '') === (b.condition || ''));
  return (blocks || []).map(b => {
    const same = b.rule ? sameAs(b) : null;
    if(b.rule && !same) told.push(b);
    return `<details class="dfold xp-rule"${b.verdict === 'never_payable' ? ' open' : ''}>
    <summary>${esc(b.title || '')}${b.period ? ` <span class="muted small">(services ${esc(b.period)})</span>` : ''}</summary>
    <div class="small ${XP_RULE_CLS[b.verdict] || 'muted'}"><b>${esc(b.verdict_text || '')}</b></div>
    ${(b.evidence_text || []).length ? `<div class="small" style="margin-top:4px"><b>What the remittances show</b>${b.as_of ? ` <span class="muted">(the run of ${esc(longDate(String(b.as_of).slice(0,10)))})</span>` : ''}<ul>${b.evidence_text.map(t => `<li>${esc(t)}</li>`).join('')}</ul></div>`
      : (b.evidence_note ? `<div class="small muted">${esc(b.evidence_note)}</div>` : '')}
    ${same ? `<div class="small muted" style="margin-top:4px">The payer's rule and what the note or the claim must show: the same as for ${esc(same.code)} above.</div>` : `
    ${b.rule ? `<div class="small" style="margin-top:4px"><b>The payer's rule:</b> ${esc(b.rule)}</div>` : ''}
    ${b.what_to_show ? `<div class="small" style="margin-top:4px"><b>What the note or the claim must show:</b> ${esc(b.what_to_show)}</div>` : ''}
    ${b.condition ? `<div class="small sev-medium">${esc(b.condition)}</div>` : ''}
    ${(b.sources || []).length ? `<div class="small" style="margin-top:4px"><b>Sources</b> <span class="muted">(each read twice${b.researched ? ', researched ' + esc(b.researched) : ''})</span>${xpRuleSources(b.sources)}</div>` : ''}`}
  </details>`;
  }).join('');
}
// "Why did this change?": the run's decisions on this item (built later, GET /api/findings/{id}/decisions); hidden until it answers
async function xpDecisions(id, el){
  if(!el) return;
  let r; try { r = await api('/api/findings/' + encodeURIComponent(id) + '/decisions'); } catch(e){ el.hidden = true; return; }
  const items = (r && r.items) || [];
  if(!items.length){ el.hidden = true; return; }
  const change = c => Object.entries(c || {}).map(([k, v]) => `${esc(k)}: ${esc(Array.isArray(v) ? v[0] : '')} → <b>${esc(Array.isArray(v) ? v[1] : v)}</b>`).join('; ');
  el.innerHTML = `<details class="dfold"><summary>Why did this change?</summary><table class="small"><tbody>${items.map(d => `<tr><td class="day">${esc(atTime(d.at))}</td><td>${change(d.change)}</td><td class="muted">${esc(d.rule || '')}${d.rule_version ? ' v' + esc(d.rule_version) : ''}</td></tr>`).join('')}</tbody></table></details>`;
  el.hidden = false;
}
// the next step's buttons. Each changes what it says it changes and nothing else: a status (with its note), a line in the
// item's history, the clipboard, or a page to open. A fax goes only after the packet has been read, to the confirmed number.
function xpBind(f, x, r, d, done){
  x = xpEffective(f, x);
  const nx = ((x || {}).explain || {}).next_step || {};
  const url = xpEmaUrl(f, x, nx);
  const err = msg => { const e = d.querySelector('#xpErr'); if(e) e.textContent = msg; };
  const say = msg => { const m = d.querySelector('#xpMsg'); if(m){ m.className = 'small ok'; m.textContent = msg; } };
  const copy = async (text, what) => { try { await navigator.clipboard.writeText(text); say(`${what} copied.`); return true; } catch(e){ err('Could not copy here: select the text and copy it by hand.'); return false; } };
  // every change carries the status this panel showed (hotfix 2, H3): one made elsewhere since is not undone (409 changed)
  const status = async (st, notes, extra, o) => { await post('/api/findings/' + f.finding_id + '/status', Object.assign({status: st, seen_status: f.status}, notes ? {notes} : {}, extra || {})); state.brief = null; state.dirty = true; done(st, o); };
  // a status a form took, checked as the menu's are (a reason for Let go and Lost, an amount for Paid): why it was not sent, or nothing
  const saveBy = async (st, given) => { const a = stAnswers(f, st, given, x); if(a.why) return a.why; await status(st, a.notes, a.extra); return null; };
  const form = spec => stForm(d, spec);
  const event = async (ev, note) => post('/api/findings/' + f.finding_id + '/event', {event: ev, note: note || ''});
  // a copy's line in the item's history: when the site refuses it (a panel drawn before a run gave the item a new number),
  // the panel says so; the copy itself stands (review people-6)
  const logged = async (ev, what) => { try { await event(ev); } catch(e){ err(`${what} copied, but not kept in its history: ${e.message || 'no answer from the site'}`); } };
  const fold = id => { const el = d.querySelector(id); if(el){ el.open = true; el.scrollIntoView({block:'start'}); } };
  const eraText = (nx.steps || []).find(s => /ERA of the check/.test(s)) || '';
  const act = {
    build_packet: () => { if(d.querySelector('#noteGo') && ['note'].includes(kindFor(state.brief || {}, f))) fold('#builderFold'); else { const b = d.querySelector('#btnAppeal'); if(b) b.click(); else fold('#builderFold'); } },
    attach_note: () => { fold('#builderFold'); const i = d.querySelector('#noteFile'); if(i) i.focus(); },
    find_note: () => { fold('#builderFold'); if(d.querySelector('#noteEma')) noteFromEma(f.finding_id, d); },
    upload_note: () => { window.open('/upload', '_blank', 'noopener'); },
    fax_payer: () => xpFaxPreview(f, r.packets, d),
    copy_instructions: async () => { if(await copy(nx.instructions || nx.action || '', 'The steps')) await logged('instructions', 'The steps'); },
    request_era: async () => { if(!eraText){ err('No ERA request for this item.'); return; } if(await copy(eraText, 'The ERA request')) await logged('era_requested', 'The ERA request'); },
    mark_sent: () => form(stSpec(f, 'appealed', (st, g) => saveBy(st, {note: g.note || 'Sent from the next step'}), x)),
    log_outcome: () => xpOutcome(f, d, status, err, x),
    decide: () => xpDecide(f, d, status, err, act),
    mark_posted: () => nx.route === 'post'
      ? form({title: 'Mark it posted in EMA?', help: 'It leaves the list, closed as Paid with $0.00 recovered: the money already came.', button: 'Mark posted',
          save: async () => { await status('resolved', 'Posted in EMA', {recovered_cents: 0}); return null; }})
      : form({title: 'What did you post in EMA?', help: 'The remittance or the takeback. It is kept in its history; the item stays on the list.', button: 'Save',
          fields: [{id: 'stNote', key: 'note', label: 'What you posted'}], save: async v => { await event('posted', v.note.trim()); stFormClose(d); say('Noted in its history.'); return null; }}),
    mark_billed: () => form({title: 'Mark it billed', help: `Who was billed (${nx.route === 'bill_patient' ? 'the patient' : 'the other payer'}), and when? It is kept in its history.`, button: 'Mark billed',
      fields: [{id: 'stNote', key: 'note', label: 'Who was billed, and when'}],
      save: async v => { const n = v.note.trim(); if(!n) return 'Not changed: say who was billed.'; await event('billed', n); await status('appealed', 'Billed: ' + n); return null; }}),
    // a service the payer put on the patient (the owner's decision 2): one change each, the server's own words in its history
    billed_patient: () => form({title: 'Billed the patient', help: "It waits for the patient's payment in EMA and closes when EMA shows it.", button: 'Billed the patient',
      fields: [{id: 'stNote', key: 'note', label: 'How, and anything to remember (optional)'}],
      save: async v => { await status('appealed', 'Billed the patient' + (v.note.trim() ? ': ' + v.note.trim() : '')); return null; }}),
    mark_done: () => form({title: 'Mark it done', help: 'It leaves the list and counts as done by you, with nothing recovered.', button: 'Mark done',
      fields: [{id: 'stNote', key: 'note', label: 'Why (optional)'}], save: async v => { await status('resolved', v.note.trim(), {recovered_cents: 0, done: true}); return null; }}),
    accept_pattern: () => xpAccept(f, nx, err, say, d),
    let_go: () => form(stSpec(f, 'dismissed', saveBy, x)),
    reopen: () => form({title: 'Reopen it?', help: 'It goes back on the list as To do.', button: 'Reopen', save: async () => { await status('open', 'Reopened'); return null; }}),
    open_in_ema: () => { if(url) window.open(url, '_blank', 'noopener,noreferrer'); else err('No EMA address is set for this site.'); },
  };
  // one press at a time, and none on a panel the walk opened a moment ago (H1: the status bar's walk guard)
  d.querySelectorAll('[data-xp]').forEach(b => b.onclick = async () => { const fn = act[b.dataset.xp]; if(!fn || b.disabled || xpWalkGuarded()) return; err(''); b.disabled = true;
    try { await fn(); } catch(e){ err('Not done: ' + (e.message || 'no answer from the site') + '. Try again.'); } finally { b.disabled = false; } });
  d.querySelectorAll('a.xp-rows').forEach(a => a.onclick = e => { e.preventDefault(); const st = d.querySelector('#xpStory'); if(!st) return; st.open = true;
    const first = st.querySelector(`tr[data-n="${(a.dataset.rows || '').split(',')[0]}"]`); (first || st).scrollIntoView({block:'center'}); });
  xpRouteBind(f, x, r, d, {status, err, say, copy, act, event});
}
// Decide what to do, for an item whose next step is a decision (route decide): it needed a fix and was sent again (Sent,
// with what went), the payer is wrong (the appeal: its packet or letter), or the payer is right (Let go, with the reason)
function xpDecide(f, d, status, err, act){
  const box = d.querySelector('#xpMsg'); if(!box) return;
  box.className = 'small';
  box.innerHTML = `<div class="xp-log"><b>What did you decide?</b>
    <label><input type="radio" name="xpd" value="fixed" checked> It needed a fix: I corrected it and sent it again</label>
    <label><input type="radio" name="xpd" value="appeal"> The payer is wrong: appeal it</label>
    <label><input type="radio" name="xpd" value="let_go"> The payer is right: let it go</label>
    <input id="xpdNote" class="xp-input" maxlength="300" placeholder="What you sent, or why (kept in its history)">
    <div class="actions"><button class="primary" id="xpdSave">Go</button><button class="ghost" id="xpdCancel">Cancel</button></div></div>`;
  box.querySelector('#xpdCancel').onclick = () => { box.innerHTML = ''; };
  const save = box.querySelector('#xpdSave');
  save.onclick = async () => {
    if(save.disabled || xpWalkGuarded()) return;      // H1: one change per press, none on a panel the walk just opened
    save.disabled = true;
    const what = (box.querySelector('input[name=xpd]:checked') || {}).value, note = box.querySelector('#xpdNote').value.trim();
    try {
      if(what === 'appeal'){ box.innerHTML = ''; await act.build_packet(); return; }
      if(what === 'let_go'){ if(!note){ err('Say why it is let go: the reason is kept in its history.'); return; } await status('dismissed', note); return; }
      await status('appealed', 'Corrected and sent again' + (note ? ': ' + note : ''));
    } catch(e){ err('Not saved: ' + (e.message || 'no answer from the site')); }
    finally { save.disabled = false; }
  };
}
// the status bar's walk guard (index.html walkGuarded): a panel "open the next" opened a moment ago takes no press, since
// the second click of a double click lands on the next item's button in the same place
function xpWalkGuarded(){ return typeof walkGuarded === 'function' && walkGuarded(); }
// Log what happened (a call or a portal check, key n): what the payer said as a chip (keys 1 to 6), the reference number and
// what they said. Paid asks the amount (read as the remittance prints it, empty at first: H4) and Denied the code. Each answer
// is a status the rules know: in process waits on the payer (Sent); Paid is Paid; denied, no claim on file, records asked and a
// wrong payer stay to do (Started) with the answer in the item's history, where its next step is read. Saving opens the next item.
const CALL_ANSWERS = [['in_process', 'In process'], ['paid', 'Paid'], ['denied', 'Denied'], ['no_claim', 'No claim on file'], ['records', 'Needs records'], ['wrong_payer', 'Wrong payer']];
const CALL_WORDS = {denied: 'denied', no_claim: 'no claim on file: fix it and send it again', records: 'the payer needs records: appeal with the visit note', wrong_payer: 'wrong payer: bill the right payer'};
function xpOutcome(f, d, status, err, x){
  const box = d.querySelector('#xpMsg'); if(!box) return;
  const zero = paidZeroOk(f, x), xb = d.querySelector('#xpBox');
  box.className = 'small';
  box.innerHTML = `<div class="st-form call-form" id="callForm" role="group" aria-label="What the payer said">
    <div class="st-form-head"><b>What did the payer say?</b><span>Keys 1 to 6 pick the answer; Enter saves.</span></div>
    <div class="chip-opts" role="radiogroup" aria-label="The payer's answer">${CALL_ANSWERS.map(([k, w], i) => `<button type="button" class="chip-opt" role="radio" aria-checked="${i === 0}" aria-pressed="${i === 0}" data-ans="${k}" data-ans-key="${i + 1}"><kbd>${i + 1}</kbd> ${esc(w)}</button>`).join('')}</div>
    <div class="field" data-when="paid" hidden><label for="callAmt">Amount the payer paid, in dollars</label><input id="callAmt" inputmode="decimal" maxlength="20" autocomplete="off">
      <span class="hint">${f.shortfall_cents != null ? `The amount in dispute is ${money2(f.shortfall_cents)}.` : ''}${zero ? ' Type 0 if the money came before this item was listed.' : ''}</span></div>
    <div class="field" data-when="denied" hidden><label for="callCode">The denial code</label><input id="callCode" maxlength="20" autocomplete="off"></div>
    <div class="field"><label for="callRef">Reference number (optional)</label><input id="callRef" maxlength="60" autocomplete="off"></div>
    <div class="field"><label for="callNote">What they said</label><input id="callNote" maxlength="300" autocomplete="off"></div>
    <div class="hint">The next run dates the follow-up from the payer's answer window.</div>
    <div class="actions"><button type="button" class="primary" id="callSave">Save and open the next</button><button type="button" class="ghost" id="callCancel">Cancel</button></div></div>`;
  if(xb && xb.classList) xb.classList.add('has-form');
  let what = 'in_process';
  const q = sel => box.querySelector(sel);
  box.querySelectorAll('[data-ans]').forEach(b => b.onclick = () => { what = b.dataset.ans;
    box.querySelectorAll('[data-ans]').forEach(o => { const on = o === b; o.setAttribute('aria-checked', on ? 'true' : 'false'); o.setAttribute('aria-pressed', on ? 'true' : 'false'); });
    box.querySelectorAll('[data-when]').forEach(el => { el.hidden = el.dataset.when !== what; });
    const first = q(`[data-when="${what}"] input`) || q('#callNote'); if(first && first.focus) first.focus(); });
  q('#callCancel').onclick = () => { box.innerHTML = ''; if(xb && xb.classList) xb.classList.remove('has-form'); if(d.focus) d.focus({preventScroll: true}); };
  const save = q('#callSave');
  const go = async () => {
    if(save.disabled || xpWalkGuarded()) return;      // H1: one change per press, none on a panel the walk just opened
    save.disabled = true;
    const val = sel => String((q(sel) || {}).value || '').trim(), ref = val('#callRef'), note = val('#callNote'), code = val('#callCode');
    const said = [ref ? 'Reference ' + ref : '', note].filter(Boolean).join('. ');
    try {
      if(what === 'paid'){
        const got = paidCents(val('#callAmt'), f.charge_cents, zero);
        if(got.cents == null){ err(got.why); return; }
        await status('resolved', 'The payer paid' + (said ? ': ' + said : ''), {recovered_cents: got.cents}, {walk: true});
      } else if(what === 'in_process'){
        if(!said){ err('Say what the payer said, or give its reference number.'); return; }
        await status('appealed', 'Call or portal check: in process. ' + said, null, {walk: true});
      } else {
        if(what === 'denied' && !code && !note){ err('Give the denial code, or say what the payer said.'); return; }
        await status('in_progress', 'Call or portal check: ' + CALL_WORDS[what] + (code ? ' (' + code + ')' : '') + (said ? '. ' + said : ''), null, {walk: true});
      }
    } catch(e){ err('Not saved: ' + (e.message || 'no answer from the site')); }
    finally { save.disabled = false; }
  };
  save.onclick = go;
  q('#callForm').onkeydown = e => { if(e.key === 'Enter' && e.target && e.target.tagName === 'INPUT'){ e.preventDefault(); go(); } };
  const first = q('#callNote'); if(first && first.focus) first.focus();
}
// Accept a write-off pattern: the write-off policy table (one decision for every item of the pattern), never one item
// quietly. It carries the owner's password where the page holds one, and asks for it when the site wants it (as a payer's
// fax number does: faxes.js faxOwnerPost), so the owner's gate holds wherever the site sets one
async function xpAccept(f, nx, err, say, d){
  const pattern = nx.pattern;
  if(!pattern){ state.tab = 'policy'; location.hash = 'policy'; if(typeof closeDetail === 'function') closeDetail(); render(); return; }
  const pp = pattern.endsWith('|payer_policy') ? pattern.split('|') : null;
  const words = pp ? `Accept that ${pp[0]} does not pay ${pp[1]}${pp[2] ? ' (services ' + pp[2] + ')' : ''}? Every such denial leaves Work now at the next run and stays counted on the Write-off policy page, with who decided, where Pursue brings them back.`
                 : `Accept every write-off like this one (${pattern.split('|').filter(Boolean).join(', ')}) as policy? Its items leave the work list and stay findable. The decision shows on the Write-off policy page with who made it, where it can be changed.`;
  return stForm(d, {title: 'Accept as policy?', help: words, button: 'Accept as policy', save: async () => {
    const where = document.querySelector('#xpMsg');
    if(typeof faxOwnerPost === 'function') await faxOwnerPost('/api/ema/policy', {pattern, decision: 'accept'}, where);
    else await post('/api/ema/policy', {pattern, decision: 'accept'});
    stFormClose(d);
    say('Accepted as policy: the next run takes these items off the work list.'); return null; }});
}
// Fax it to the payer: what goes is read first (the packet's letter and PDF; for a rate request, the letter), then sent as it
// is to the payer's confirmed number. A hold comes back in words, with the one answer a person may give where there is one
// (still unpaid in EMA; the code went out as billed).
async function xpFaxPreview(f, packets, d){
  const pk = (packets || [])[0], kind = state.brief ? kindFor(state.brief, f) : '';
  if(!pk && kind !== 'rate'){
    const b = d && d.querySelector('#builderFold'); if(b){ b.open = true; b.scrollIntoView({block:'start'}); }
    const m = d && d.querySelector('#fxOneMsg'); const text = 'Build the packet first (below), so you can read it before it goes.';
    if(m){ m.className = 'small sev-medium'; m.textContent = text; } else { const e = d && d.querySelector('#xpErr'); if(e) e.textContent = text; }
    return;
  }
  // the packet's "Before you send it" checklist (the payer's published rule for the code, the owner's decision 4): shown
  // here, where the fax goes out, never in what is faxed
  let letter = '', checklist = [];
  try { const got = pk ? await api('/api/packets/' + encodeURIComponent(pk.packet_id)) : await api('/api/appeal/' + encodeURIComponent(f.finding_id));
        letter = (got && got.letter) || ''; checklist = (got && Array.isArray(got.checklist)) ? got.checklist : []; } catch(e){}
  $('#modalBox').innerHTML = `<div style="display:flex;justify-content:space-between;gap:8px"><h2 style="margin:0">Fax to ${esc(f.payer_group || 'the payer')}</h2><button id="closeM" aria-label="Close">✕</button></div>
    <div class="note">Read what goes, then send it. It goes as it is to the payer's confirmed appeal fax number, and the item is marked Sent.</div>
    ${checklist.length ? `<div class="note sev-medium" id="xpFaxCheck"><b>Before you send it</b>${checklist.map(c => `<div>${esc(c)}</div>`).join('')}</div>` : ''}
    <div class="actions">${pk ? `<a href="/api/packets/${encodeURIComponent(pk.packet_id)}/pdf${pk.note_hold ? '?check=1' : ''}" target="_blank" rel="noopener"><button>Open the PDF</button></a>` : ''}<button class="primary" id="xpFaxGo">Send the fax</button></div>
    <div id="xpFaxMsg" class="small"></div><pre>${esc(letter)}</pre>`;
  $('#modal').classList.add('open');
  $('#closeM').onclick = () => $('#modal').classList.remove('open');
  // this preview's mark: an answer that comes after the person closed it, or opened another item's, is said in a notice and
  // never closes or rewrites the window now showing (hotfix 2, H1: a late answer never acts on another item)
  const mine = 'fx' + (xpFaxPreview.n = (xpFaxPreview.n || 0) + 1);
  $('#modalBox').dataset.fax = mine;
  const here = () => $('#modal').classList.contains('open') && $('#modalBox').dataset.fax === mine;
  const what = `${f.payer_group || 'the payer'}${f.shortfall_cents ? ' · ' + money2(f.shortfall_cents) : ''}`;
  const said = pk ? {packet_id: pk.packet_id} : {};
  let busy = false;
  const go = async () => { const bt = $('#xpFaxGo'), msg = $('#xpFaxMsg');
    if(busy) return;                                  // one send at a time: a second click does nothing (hotfix 2)
    busy = true; bt.disabled = true; msg.querySelectorAll('button').forEach(x => x.disabled = true);
    msg.className = 'small muted'; msg.textContent = 'Sending…';
    let res; try { res = await post('/api/appeals/send', Object.assign({finding_id: f.finding_id}, said)); }
    catch(e){ busy = false;
      if(!here()){ if(typeof notice === 'function') notice(`Appeal by fax (${what}): ${e.message}`); return; }
      msg.className = e.code === 'in_flight' ? 'small sev-medium' : 'small sev-high'; msg.textContent = e.message; bt.disabled = false; return; }
    busy = false;
    if(res.status === 'sent'){ state.brief = null; state.dirty = true;
      if(!here()){ if(typeof notice === 'function') notice(`Faxed: ${what}${res.number ? ' to ' + res.number : ''}. ${res.kept || 'It is marked Sent.'}`); return; }
      $('#modal').classList.remove('open');
      // kept: the item changed while the fax went out (the run's Paid, a hide), so it was not marked Sent; its history says so
      if(res.kept && typeof notice === 'function') notice(`Faxed${res.number ? ' to ' + res.number : ''}. ${res.kept}`);
      openDetail(f.finding_id); return; }
    if(!here()){ if(typeof notice === 'function') notice(`Not faxed (${what}): ${res.reason || 'Not sent.'} Open it again to finish.`); return; }
    msg.className = 'small sev-medium'; msg.textContent = res.reason || 'Not sent.'; bt.disabled = false;
    if(res.code === 'note_check' && res.confirm){
      // the note is this patient's visit only when a person opened it and said so (hotfix 2: wrong-patient notes)
      msg.insertAdjacentHTML('beforeend', `<div style="margin-top:4px">${res.packet_id ? `<a href="/api/packets/${encodeURIComponent(res.packet_id)}/pdf?check=1" target="_blank" rel="noopener">Open the packet</a> ` : ''}<label><input type="checkbox" id="xpNoteOk"> I opened this note: it is this patient's visit on ${esc(res.visit_date ? longDate(res.visit_date) : 'the date of service')}</label> <button id="xpNoteGo" disabled>Fax it</button></div>`);
      const ok = msg.querySelector('#xpNoteOk'), send = msg.querySelector('#xpNoteGo');
      ok.onchange = () => { send.disabled = !ok.checked; };
      send.onclick = () => { if(send.disabled || !ok.checked) return; said.note_checked = true; said.packet_id = said.packet_id || res.packet_id; go(); };
      return;
    }
    // the one answer a person may give where there is one: still unpaid in EMA, the code went out as billed, or it was
    // checked and is still unpaid (EMA shows it settled, or the archive shows it likely paid)
    const answer = {stale: ['Still unpaid in EMA: fax it anyway', 'stale_ok'], check_code: ['It went out as billed: fax it', 'confirmed'],
      check_first: ['Checked: still unpaid, fax it', 'checked']}[res.code];
    if(answer){
      const again = document.createElement('button'); again.textContent = answer[0]; again.id = 'xpAgain';
      again.onclick = () => { said[answer[1]] = true; again.remove(); go(); };
      msg.appendChild(document.createTextNode(' ')); msg.appendChild(again);
    } };
  $('#xpFaxGo').onclick = go;
}

// ================================================================================ the item panel (DESIGN P3, unit ui-panel)
// The panel's head (where you are, the title, the patient behind the names switch, the payer, the date of service and the
// code, the claim ID to copy, the state beside the person's status as one button, the money at stake and the due date), the
// small forms that take a status's answers in the panel (no browser box: Enter saves, Esc cancels, a reason as a chip), the
// toast with Undo, the keys, and the answers kept for the next items so Next opens at once. Nothing here decides a state, an
// outcome or a closure: the rules do, and the server holds to every rule a form shows (a reason for Let go and Lost, an
// amount for Paid, the status the panel showed).
const PANEL_ICON = n => `<svg class="i" aria-hidden="true"><use href="/static/icons.svg#i-${n}"/></svg>`;
// the answers for an item (the item and its next step), asked once and kept two minutes for this analysis; any change a
// person makes drops them all, so a kept answer never shows an item as it was before a change
const PANEL_CACHE = new Map(), PANEL_TTL_MS = 120000;
const panelStamp = () => String(((state.meta || {}).remits || {}).last_payment || '');
function panelCacheDrop(){ PANEL_CACHE.clear(); }
function panelAnswers(id){
  const had = PANEL_CACHE.get(id);
  if(had && Date.now() - had.at < PANEL_TTL_MS && had.stamp === panelStamp()) return had;
  const enc = encodeURIComponent(id);
  const c = {at: Date.now(), stamp: panelStamp(), ready: false};
  c.item = api('/api/findings/' + enc);
  c.told = api('/api/findings/' + enc + '/explain').catch(e => ({explain_error: 'What to do could not be worked out just now (' + (e.message || 'no answer') + ').'}));
  // a refused or failed answer is never kept: the next open asks again (and says why)
  c.item.then(() => { c.ready = true; }, () => { if(PANEL_CACHE.get(id) === c) PANEL_CACHE.delete(id); });
  PANEL_CACHE.set(id, c);
  return c;
}
// the next two items of the list (and the one before) are asked for while this one is read
function panelPrefetch(id){
  const ids = state.worklist || [], i = ids.indexOf(id);
  if(i < 0) return;
  [i + 1, i + 2, i - 1].forEach(k => { if(k >= 0 && ids[k] && !PANEL_CACHE.has(ids[k])) panelAnswers(ids[k]); });
}
function panelPlace(id){
  const ids = state.worklist || [], i = ids.indexOf(id);
  return {i, n: Math.max(state.wlTotal || 0, ids.length), prev: i > 0,
    next: i >= 0 && (i < ids.length - 1 || (!!state.wlNext && (state.wlTotal || 0) > ids.length))};
}
// where you are: the list (or the plan's step, state.queueLabel) and the item's place in it; the state is said once, beside
// the status, never here as well
function panelCrumb(id){
  const p = panelPlace(id), desk = typeof DESKS !== 'undefined' && DESKS[state.tab] ? DESKS[state.tab].title : '';
  const where = state.queueLabel || desk || '';
  if(p.i < 0) return `<b>${esc(where || 'Item')}</b>`;
  return `${where ? `<b>${esc(where)}</b> · ` : ''}Item ${p.i + 1} of ${Number(p.n).toLocaleString()}`;
}
function panelNavHtml(p){
  return `<button type="button" id="prevItem" aria-label="Previous item (k)" title="Previous item (k)"${p.prev ? '' : ' disabled'}>${PANEL_ICON('chev-l')}</button>`
    + `<button type="button" id="nextItem" aria-label="Next item (j)" title="Next item (j)"${p.next ? '' : ' disabled'}>${PANEL_ICON('chev-r')}</button>`
    + `<button type="button" id="closeD" aria-label="Close (Esc)" title="Close (Esc)">${PANEL_ICON('x')}</button>`;
}
// until the item's answer comes: the head's frame and quiet placeholders that show only after a moment (never a blank word)
function panelSkeletonHtml(id){
  return `<div class="dr-head" aria-busy="true"><div class="dr-top"><span class="crumb">${panelCrumb(id)}</span>${panelNavHtml(panelPlace(id))}</div>
    <div class="dr-skel"><span class="skel skel-title"></span><span class="skel skel-line"></span><span class="skel skel-facts"></span></div></div>
    <div class="dr-body dr-skel"><span class="skel skel-box"></span><span class="skel skel-line"></span><span class="skel skel-line"></span></div>`;
}
// the due date as the item's state words it: a follow-up is a day to follow up ("today", "in 3 days"), never a window that
// closes; a window that closed says so; an item that is over has nothing due
function panelDue(f, x){
  const ex = (x && x.explain) || {}, vd = ex.verdict || {}, nx = ex.next_step || {}, st = ex.state || {};
  const ws = vd.state || st.work_state || f.work_state;
  const follow = ws === 'waiting' && (vd.follow_up_on || st.follow_up_on || f.follow_up_on);
  if(follow){
    const n = typeof daysUntil === 'function' ? daysUntil(follow) : null;
    const words = n == null ? '' : n < -1 ? `${-n} days ago` : n === -1 ? 'yesterday' : n === 0 ? 'today' : n === 1 ? 'tomorrow' : `in ${n} days`;
    return {label: 'Follow up', day: follow, words, now: n != null && n <= 0};
  }
  if(['done', 'accepted'].includes(ws)) return {label: 'Due', day: null, words: 'Nothing due', now: false};
  const day = nx.deadline || f.appeal_deadline;
  if(!day) return {label: 'Due', day: null, words: 'No date', now: false};
  if(ws === 'expired') return {label: 'Window closed', day, words: '', now: false};
  const c = closesText(day);
  return {label: 'Due', day, words: c.text, now: c.cls === 'due-now'};
}
function panelFactsHtml(f, x){
  const nx = ((x && x.explain) || {}).next_step || {}, due = panelDue(f, x);
  const stake = nx.expected_cents != null ? nx.expected_cents : f.shortfall_cents;
  return `<div><dt>At stake</dt><dd class="money">${stake != null ? money2(stake) : 'Not known'}</dd></div>
    <div><dt>${esc(due.label)}</dt><dd${due.now ? ' class="now"' : ''}>${due.day ? esc(longDate(due.day)) + ' ' : ''}<span>${esc(due.words)}</span></dd></div>`;
}
// the head: where you are with Previous, Next and Close; the title; the patient (names on only), the payer, the date of service,
// the code and the claim ID (one click, or y, copies it); the state beside the person's status; At stake and Due
function panelHeadHtml(f, r, x){
  r = r || {}; x = xpEffective(f, x || {}) || {};
  const ex = x.explain || {}, ws = (ex.verdict || {}).state || (ex.state || {}).work_state || f.work_state, cl = r.claim || {};
  const plain = state.brief && state.brief.labels ? labelOf(state.brief, f.signal).label : (f.title || 'Item');
  const phi = typeof showPhi === 'function' && showPhi();
  const claimNo = f.patient_control_number || cl.payer_claim_control_number || '';
  const meta = [phi && cl.patient ? `<span class="who">${esc(cl.patient)}</span>` : '',
    f.payer_group ? `<span>${esc(f.payer_group)}</span>` : '',
    f.service_date ? `<span title="Date of service">${esc(longDate(String(f.service_date).slice(0, 10)))}</span>` : '',
    f.procedure_code ? `<span title="Code">${esc(f.procedure_code)}</span>` : '',
    claimNo ? `<button type="button" class="copy-inline" id="copyClaim" data-copy="${esc(claimNo)}" title="Copy the claim ID (y)">${esc(claimNo)}</button>` : ''].filter(Boolean);
  return `<div class="dr-head">
    <div class="dr-top"><span class="crumb">${panelCrumb(f.finding_id)}</span>${panelNavHtml(panelPlace(f.finding_id))}</div>
    <h2 class="dr-title" id="drTitle" tabindex="-1">${esc(plain)}</h2>
    ${meta.length ? `<div class="dr-meta">${meta.join('<span class="sep" aria-hidden="true"></span>')}</div>` : ''}
    <div class="dr-state"><span id="xpChips">${xpChips(f, ws)}</span>${stMenuHtml(f)}${f.worked_by && f.worked_to === f.status ? `<span class="small muted by">by ${esc(f.worked_by)}</span>` : ''}</div>
    <dl class="facts facts-2" id="drFacts">${panelFactsHtml(f, x)}</dl></div>`;
}
// the head's due date and money follow the next step once it comes (its deadline, its amount)
function panelFacts(f, x, d){ const el = d && d.querySelector && d.querySelector('#drFacts'); if(el) el.innerHTML = panelFactsHtml(f, xpEffective(f, x)); }

// ---------------------------------------------------------------- the person's status: one button, its menu, its forms
// Started and back to To do are saved at once (with Undo); Sent, Paid, Lost and Let go open a small form. Lost and Let go sit
// below a line, apart from the rest, and always ask why.
const ST_ITEMS = [['in_progress', 'Started', ''], ['appealed', 'Sent', 'S'], ['resolved', 'Paid', 'P'], '-', ['lost', 'Lost', ''], ['dismissed', 'Let go', 'L'], '-', ['open', 'Back to To do', '']];
const ST_SAID = {open: 'Back to To do', in_progress: 'Marked started', appealed: 'Marked sent', resolved: 'Marked paid', lost: 'Marked lost', dismissed: 'Let go'};
// the reasons a person gives most for money given up (the owner's weekly count by payer and reason), a chip each
const ST_REASONS = ['The payer is right: not covered', "Patient's share: billed the patient", "Small balance under the owner's limit", 'Duplicate claim',
  "Past the payer's limit", 'Already paid in EMA'];
function stMenuHtml(f){
  const rows = ST_ITEMS.filter(it => it === '-' || it[0] !== f.status)
    .map(it => it === '-' ? '<hr>' : `<button type="button" role="menuitem" data-st="${it[0]}"${['lost', 'dismissed'].includes(it[0]) ? ' class="danger-item"' : ''}>${esc(it[1])}${it[2] ? `<kbd class="kbd-right">${it[2]}</kbd>` : ''}</button>`)
    .join('').replace(/^(<hr>)+|(<hr>)+$/g, '').replace(/(<hr>){2,}/g, '<hr>');
  return `<span class="menu-wrap st-wrap"><button type="button" class="st-btn" id="stBtn" aria-haspopup="menu" aria-expanded="false" aria-controls="stMenu" title="Change the status">${esc(STATUS_WORDS[f.status] || f.status || 'To do')}${PANEL_ICON('chev-d')}</button>
    <div class="menu st-menu" id="stMenu" role="menu" aria-label="Change the status" hidden>${rows}</div></span>`;
}
// Let go and Lost take a reason on every item (the server holds to it); a claim no payer answered says it may not have been
// asked for yet. The words are the ones the list's Let go uses (desk.js letGoNote).
function stReasonWhy(signal, status, note){
  if(!['dismissed', 'lost'].includes(status) || String(note || '').trim()) return null;
  const claim = typeof CLAIM_CHASES !== 'undefined' && CLAIM_CHASES.has(signal), one = typeof ONE_AT_A_TIME !== 'undefined' && ONE_AT_A_TIME.has(signal);
  return claim ? 'Not changed. A claim with no answer from a payer needs a note that says why.' : one ? 'Not changed. This item needs a note that says why.'
    : 'Not changed. Letting an item go, or marking it lost, needs a reason.';
}
function stReasonHelp(signal, status){
  const claim = typeof CLAIM_CHASES !== 'undefined' && CLAIM_CHASES.has(signal), lost = status === 'lost';
  return (claim ? 'No answer from a payer is on file for it, so this money may not have been asked for yet. ' : '')
    + `It leaves the list${lost ? '' : ' and counts as let go'}. Pick a reason or write one: it is kept in its history.`;
}
// what a status change sends, from what its form took: {why} when it is not enough (nothing is sent), else the note and the
// amount. Paid's amount is read as the remittance prints it and starts empty (H4); $0 only where nothing new was owed.
function stAnswers(f, st, given, x){
  given = given || {};
  const note = String(given.note || '').trim(), out = {notes: note || null, extra: {}};
  if(st === 'resolved'){
    const got = paidCents(given.amount, f.charge_cents, paidZeroOk(f, x));
    if(got.cents == null) return {why: got.why};
    out.extra.recovered_cents = got.cents;
  }
  const why = stReasonWhy(f.signal, st, note);
  return why ? {why} : out;
}
// A status set from an item's panel with what its form took (no browser box): {saved: true}, or {why} when the answers are
// not enough (nothing is sent then). given: {note, amount}. seen: the status the panel showed; the site leaves an item that
// changed since as it is (and says so).
async function setFindingStatus(id, status, shortfall, signal, seen, charge, given){
  const f = state.detailId === id && state.detailF ? state.detailF : {finding_id: id, signal, charge_cents: charge};
  const a = stAnswers(Object.assign({}, f, {signal, charge_cents: charge}), status, given, state.detailId === id ? state.detailX : null);
  if(a.why) return {why: a.why};
  await post('/api/findings/' + id + '/status', Object.assign({status}, seen ? {seen_status: seen} : {}, a.notes ? {notes: a.notes} : {}, a.extra));
  return {saved: true};
}
function stFormClose(d){
  const fm = d && d.querySelector && d.querySelector('#stForm');
  if(fm){ fm.hidden = true; fm.innerHTML = ''; }
  if(d && d.classList) d.classList.remove('dr-form-open');
  if(d && d.focus) d.focus({preventScroll: true});
}
// One small form under the head: a title, a line of help, the reason chips, the fields, Save and Cancel. Focus starts in the
// first field; Enter in a field saves; Esc cancels (index.html's Escape). spec.save(values) returns why it was not saved, or
// nothing. While it saves, Save says so and takes no second press.
function stForm(d, spec){
  const fm = d && d.querySelector && d.querySelector('#stForm'); if(!fm) return;
  const chips = (spec.chips || []).map((c, i) => `<button type="button" class="chip-opt" aria-pressed="false" data-chip="${i}">${esc(c)}</button>`).join('');
  const fields = (spec.fields || []).map(x => `<div class="field"><label for="${x.id}">${esc(x.label)}</label><input id="${x.id}" type="text" maxlength="${x.max || 300}" autocomplete="off"${x.mode ? ` inputmode="${x.mode}"` : ''}>${x.hint ? `<span class="hint">${esc(x.hint)}</span>` : ''}</div>`).join('');
  fm.innerHTML = `<div class="st-form-head"><b>${esc(spec.title)}</b>${spec.help ? `<span>${esc(spec.help)}</span>` : ''}</div>
    ${chips ? `<div class="chip-opts" role="group" aria-label="Reason">${chips}</div>` : ''}${fields}
    <div class="st-form-err" id="stFormErr" role="alert"></div>
    <div class="actions"><button type="button" class="primary" id="stSave">${esc(spec.button)}</button><button type="button" class="ghost" id="stCancel">Cancel</button></div>`;
  fm.hidden = false; d.classList.add('dr-form-open');
  let chip = null;
  fm.querySelectorAll('[data-chip]').forEach(b => b.onclick = () => {
    const on = b.getAttribute('aria-pressed') !== 'true';
    fm.querySelectorAll('[data-chip]').forEach(o => o.setAttribute('aria-pressed', 'false'));
    b.setAttribute('aria-pressed', on ? 'true' : 'false'); chip = on ? spec.chips[Number(b.dataset.chip)] : null; });
  const err = fm.querySelector('#stFormErr'), save = fm.querySelector('#stSave');
  const go = async () => {
    if(save.disabled || xpWalkGuarded()) return;      // one change per press, none on a panel the walk opened a moment ago
    const vals = {chip};
    (spec.fields || []).forEach(o => { vals[o.key] = fm.querySelector('#' + o.id).value || ''; });
    err.textContent = ''; save.disabled = true; const label = save.textContent; save.textContent = 'Saving…';
    let why = null;
    try { why = await spec.save(vals); }
    catch(e){ if(e.code === 'changed' || e.code === 'in_flight') state.dirty = true;
      why = 'Not saved: ' + (e.message || 'the site did not answer') + (e.code ? '' : '. Try again.'); }
    if(!fm.isConnected) return;
    save.disabled = false; save.textContent = label;
    if(why){ err.textContent = why; const first = fm.querySelector('input'); if(first) first.focus(); }
  };
  save.onclick = go;
  fm.querySelector('#stCancel').onclick = () => stFormClose(d);
  fm.onkeydown = e => { if(e.key === 'Enter' && e.target && e.target.tagName === 'INPUT'){ e.preventDefault(); go(); } };
  const first = fm.querySelector('input') || fm.querySelector('[data-chip]') || save;
  if(first) first.focus();
}
// the forms each status takes, saved through `saveWith(status, given)` (the menu's own, or the next step's)
function stSpec(f, st, saveWith, x){
  const zero = paidZeroOk(f, x);
  if(st === 'appealed') return {title: 'Mark it sent', help: 'It waits on the payer; the next run dates the follow-up.', button: 'Mark sent',
    fields: [{id: 'stHow', key: 'how', label: 'What went out and how (optional)'}, {id: 'stRef', key: 'ref', label: 'Reference number (optional)', max: 60}],
    save: v => saveWith('appealed', {note: [v.how.trim(), v.ref.trim() ? 'Reference ' + v.ref.trim() : ''].filter(Boolean).join('. ')})};
  if(st === 'resolved') return {title: 'Mark it paid', button: 'Mark paid',
    help: (f.shortfall_cents != null ? `The amount in dispute is ${money2(f.shortfall_cents)}. ` : '') + (zero ? 'Type 0 if the money came before this item was listed: nothing was recovered.' : 'Type what the payer paid.'),
    fields: [{id: 'stAmt', key: 'amount', label: 'Amount the payer paid, in dollars (from the remittance or EMA)', mode: 'decimal', max: 20}, {id: 'stNote', key: 'note', label: 'Note (optional)'}],
    save: v => saveWith('resolved', {amount: v.amount, note: v.note})};
  if(st === 'lost' || st === 'dismissed') return {title: st === 'lost' ? 'Mark it lost' : 'Let it go', help: stReasonHelp(f.signal, st), chips: ST_REASONS,
    button: st === 'lost' ? 'Mark lost' : 'Let it go', fields: [{id: 'stNote', key: 'note', label: 'Why, in your words'}],
    save: v => saveWith(st, {note: [v.chip, v.note.trim()].filter(Boolean).join(': ')})};
  return null;
}
// the menu's status change: the answers checked, the change sent with the status the panel showed, then the toast with Undo
// and the next item (or this one again, for Started and To do)
async function stChange(f, seq, st, given){
  const got = await setFindingStatus(f.finding_id, st, f.shortfall_cents, f.signal, f.status, f.charge_cents, given);
  if(got.why) return got.why;
  panelChanged(f, st, seq);
  return null;
}
function stOpen(f, d, seq, st){
  if(st === 'in_progress' || st === 'open'){
    if(state.stBusy) return;
    state.stBusy = true;
    stChange(f, seq, st, {note: st === 'open' ? 'Back to To do' : ''})
      .then(why => { if(why) stSay(d, why); }, e => stSay(d, 'Not saved: ' + (e.message || 'the site did not answer') + (e.code ? '' : '. Try again.')))
      .finally(() => { state.stBusy = false; });
    return;
  }
  const spec = stSpec(f, st, (s, given) => stChange(f, seq, s, given), state.detailId === f.finding_id ? state.detailX : null);
  if(spec) stForm(d, spec);
}
function stSay(d, text){ const el = d && d.querySelector && d.querySelector('.st-err'); if(el) el.textContent = text; }
// after a change: the lists are drawn again when the panel closes, kept answers are dropped, the toast says what changed
// with Undo, and the walk goes on (a status that ends the work here opens the next item; Started and To do stay)
function panelChanged(f, st, seq, o){
  o = o || {};
  const what = (state.brief && state.brief.labels ? labelOf(state.brief, f.signal).label : (f.title || 'the item')) + (f.payer_group ? ' · ' + f.payer_group : '');
  state.brief = null; state.dirty = true; panelCacheDrop();
  state.panelUndo = st === f.status ? null : {id: f.finding_id, from: f.status, to: st, cents: f.recovered_cents, at: Date.now()};
  panelToast(o.said || `${ST_SAID[st] || STATUS_WORDS[st] || st}: ${what}.`, {undo: !!state.panelUndo});
  const here = seq == null ? state.detailId === f.finding_id : (typeof panelShows !== 'function' || panelShows(seq, f.finding_id));
  if(!here || typeof openDetail !== 'function') return;
  if(o.walk || ['appealed', 'dismissed', 'resolved', 'lost'].includes(st)) return panelWalk(f.finding_id, seq);
  return openDetail(f.finding_id);
}
// the next item of the list (the next page of it when the list has one), or this item again at the end
async function panelWalk(id, seq){
  const here = () => seq == null ? state.detailId === id : (typeof panelShows !== 'function' || panelShows(seq, id));
  if(!here()) return;
  const ids = state.worklist || [], i = ids.indexOf(id);
  let nx = i >= 0 ? ids[i + 1] : null;
  if(!nx && i >= 0 && state.wlNext){ try { await state.wlNext(); } catch(e){} nx = (state.worklist || [])[i + 1]; }
  if(!here()) return;
  return nx ? openDetail(nx, {walk: true}) : openDetail(id);
}
// Undo: the status back to what it was, with the status this change set as the one seen (one made elsewhere since is kept)
async function panelUndo(){
  const u = state.panelUndo;
  if(!u || Date.now() - u.at > 10000){ panelToast('Nothing to undo.'); return false; }
  state.panelUndo = null;
  const body = {status: u.from, seen_status: u.to, notes: 'Undone: back to ' + (STATUS_WORDS[u.from] || u.from)};
  if(u.from === 'resolved' && u.cents != null) body.recovered_cents = u.cents;
  try { await post('/api/findings/' + u.id + '/status', body); }
  catch(e){ panelToast('Not undone: ' + (e.message || 'the site did not answer') + '.', {error: true, stay: true}); return true; }
  state.brief = null; state.dirty = true; panelCacheDrop();
  panelToast(`Undone: back to ${STATUS_WORDS[u.from] || u.from}.`);
  if(state.detailId === u.id && typeof openDetail === 'function') openDetail(u.id);
  return true;
}
// a toast at the bottom left (the panel is on the right): what just happened, Undo for 8 seconds; a failure stays until closed
function panelToast(text, o){
  o = o || {};
  if(typeof document === 'undefined' || !document.body) return;
  let box = document.getElementById('rwToasts');
  if(!box){ box = document.createElement('div'); box.id = 'rwToasts'; box.className = 'toasts'; box.setAttribute('role', 'status'); box.setAttribute('aria-live', 'polite'); document.body.appendChild(box); }
  const t = document.createElement('div');
  t.className = 'toast' + (o.error ? ' error' : '');
  t.innerHTML = `<span class="t-icon" aria-hidden="true"></span><span class="t-text">${esc(text)}</span>${o.undo ? '<button type="button" class="t-undo">Undo</button>' : ''}<button type="button" class="t-close" aria-label="Close">${PANEL_ICON('x')}</button>`;
  while(box.children.length > 2) box.firstElementChild.remove();
  box.appendChild(t);
  const gone = () => t.remove();
  t.querySelector('.t-close').onclick = gone;
  const u = t.querySelector('.t-undo'); if(u) u.onclick = () => { gone(); panelUndo(); };
  if(!o.stay) setTimeout(gone, o.ms || 8000);
}
// the head's buttons: the status menu (arrow keys move in it, Esc closes it), Previous and Next, the claim ID's copy
function panelBindHead(f, d, seq){
  const btn = d.querySelector('#stBtn'), menu = d.querySelector('#stMenu');
  const shut = () => { if(menu) menu.hidden = true; if(btn) btn.setAttribute('aria-expanded', 'false'); };
  if(btn && menu){
    btn.onclick = () => { const open = menu.hidden; menu.hidden = !open; btn.setAttribute('aria-expanded', open ? 'true' : 'false');
      if(open){ const b = menu.querySelector('button'); if(b) b.focus(); } };
    menu.onkeydown = e => { const all = [...menu.querySelectorAll('button')], i = all.indexOf(document.activeElement);
      if(e.key === 'ArrowDown' || e.key === 'ArrowUp'){ e.preventDefault(); const n = all[(i + (e.key === 'ArrowDown' ? 1 : all.length - 1)) % all.length]; if(n) n.focus(); } };
  }
  d.querySelectorAll('[data-st]').forEach(b => b.onclick = () => { shut(); if(b.disabled || xpWalkGuarded()) return; stOpen(f, d, seq, b.dataset.st); });
  if(!d.dataset.stOutside){ d.dataset.stOutside = '1';
    d.addEventListener('click', e => { const m = d.querySelector('#stMenu'); if(m && !m.hidden && !(e.target.closest && e.target.closest('.st-wrap'))){ m.hidden = true; const b = d.querySelector('#stBtn'); if(b) b.setAttribute('aria-expanded', 'false'); } }); }
  const cc = d.querySelector('#copyClaim');
  if(cc) cc.onclick = async () => { try { await navigator.clipboard.writeText(cc.dataset.copy || ''); cc.classList.add('copied'); panelToast('Claim ID copied.'); }
    catch(e){ const s = getSelection(), rg = document.createRange(); rg.selectNodeContents(cc); s.removeAllRanges(); s.addRange(rg); stSay(d, 'Press Ctrl+C to copy the claim ID.'); } };
  panelBindNav(f, d);
}
// Previous and Next: the list's items in its order (Next asks for the list's next page at its end)
function panelBindNav(f, d){
  const id = f.finding_id, pv = d.querySelector('#prevItem'), nx = d.querySelector('#nextItem');
  if(pv) pv.onclick = () => { const ids = state.worklist || [], i = ids.indexOf(id); if(i > 0) openDetail(ids[i - 1]); };
  if(nx) nx.onclick = async () => { const ids = state.worklist || [], i = ids.indexOf(id); if(i < 0) return;
    let n = ids[i + 1]; if(!n && state.wlNext){ try { await state.wlNext(); } catch(e){} n = (state.worklist || [])[i + 1]; }
    if(n && state.detailId === id) openDetail(n); };
}
// Esc in the panel closes what is open inside it first: the status menu, then a form, then (index.html) the panel
function panelEscape(d){
  const m = d && d.querySelector && d.querySelector('#stMenu');
  if(m && !m.hidden){ m.hidden = true; const b = d.querySelector('#stBtn'); if(b){ b.setAttribute('aria-expanded', 'false'); b.focus(); } return true; }
  const fm = d && d.querySelector && d.querySelector('#stForm');
  if(fm && !fm.hidden && fm.innerHTML){ stFormClose(d); return true; }
  const call = d && d.querySelector && d.querySelector('#callForm');
  if(call){ const box = call.parentNode; if(box) box.innerHTML = ''; const x = d.querySelector('#xpBox'); if(x) x.classList.remove('has-form'); if(d.focus) d.focus({preventScroll: true}); return true; }
  return false;
}
// The keys in an open panel (the keyboard sheet lists them; none fires while the person types in a field): j and k move,
// Enter presses the main button, s Sent, p Paid, l Let go, b Billed (each opens its form), n logs a call or portal check, e
// opens EMA, y copies the claim ID, 1 to 9 copy the route's fields (in the call form, 1 to 6 pick what the payer said), c
// copies them all, u undoes the last change.
function panelKey(e, d){
  const k = e.key, t = e.target;
  const press = sel => { const b = d.querySelector(sel); if(b && !b.disabled){ b.click(); return true; } return false; };
  const hint = text => { const el = d.querySelector('#xpErr') || d.querySelector('.st-err'); if(el) el.textContent = text; };
  if(d.querySelector('#callForm') && /^[1-6]$/.test(k)){ e.preventDefault(); press(`#callForm [data-ans-key="${k}"]`); return; }
  if(k === 'j'){ e.preventDefault(); press('#nextItem'); }
  else if(k === 'k'){ e.preventDefault(); press('#prevItem'); }
  else if(k === 'Enter'){
    const own = !t || t === d || (typeof document !== 'undefined' && t === document.body) || (t.classList && t.classList.contains('dr-title'));
    if(!own) return;
    e.preventDefault(); press('#xpBox .primary') || press('#xpMain') || press('#sentNext') || hint('Nothing to press here.');
  }
  else if(k === 's'){ e.preventDefault(); press('[data-xp="mark_sent"]') || press('#sentNext') || press('[data-st="appealed"]') || hint('Nothing to mark sent here.'); }
  else if(k === 'p'){ e.preventDefault(); press('[data-st="resolved"]') || hint('This item cannot be marked paid from here.'); }
  else if(k === 'l'){ e.preventDefault(); press('[data-xp="let_go"]') || press('#letGoNext') || press('[data-st="dismissed"]') || hint('This item cannot be let go from here.'); }
  else if(k === 'b'){ e.preventDefault(); press('[data-xp="mark_billed"]') || press('[data-xp="billed_patient"]') || hint('Nothing to mark billed here.'); }
  else if(k === 'n'){ e.preventDefault(); press('[data-xp="log_outcome"]') || hint('No call or portal check to log here.'); }
  else if(k === 'e'){ e.preventDefault(); press('[data-xp="open_in_ema"]') || press('[data-rc="ema"]') || hint('No EMA address for this item.'); }
  else if(k === 'y'){ e.preventDefault(); press('#copyClaim') || hint('No claim ID to copy here.'); }
  else if(k === 'u'){ e.preventDefault(); panelUndo(); }
  else if(k === 'c'){ e.preventDefault(); press('#rcCopyAll') || hint('No route to copy here.'); }
  else if(/^[1-9]$/.test(k)){ e.preventDefault(); press(`[data-rc-key="${k}"]`); }
}
