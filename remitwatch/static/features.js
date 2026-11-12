/* RemitWatch dashboard: alerts, breakdown explorer, progress scoreboard, feed status and the note-based appeal builder.
   Loaded before the inline script in index.html and uses its helpers ($, api, qs, esc, money, money2, chart, kpi, state, findingsTable, render). */

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

// ---------------------------------------------------------------- header: feed chip and alert bell
async function refreshHeader(){
  try {
    const [al, st] = await Promise.all([api('/api/alerts?status=new'), api('/api/sync/status')]);
    state.alertsNew = al.items; state.sync = st;
    const n = al.items.length, hi = al.items.filter(a=>a.severity==='high').length;
    $('#bellBtn').innerHTML = `Alerts <b class="${hi?'sev-high':''}">${n}</b>`;
    const behind = st.days_behind;
    const cls = behind==null ? 'muted' : (behind>14 ? 'sev-high' : (behind>7 ? 'sev-medium' : 'ok'));
    const how = st.running ? 'syncing now' : (st.dropbox_configured ? (st.mode==='serverless' ? 'checks Dropbox daily and when files arrive' : (st.interval_minutes ? `auto-sync every ${st.interval_minutes} min` : 'sync on demand')) : 'Dropbox not connected');
    $('#feedChip').innerHTML = `<span class="${cls}">●</span> newest payment ${esc(st.newest_payment_date||'–')}${behind!=null?` (${behind} days ago)`:''} · ${how}`;
  } catch(e){ /* older store without these tables */ }
}

// ---------------------------------------------------------------- alerts
const ALERT_KIND = {RAMP:'Ramping up', RISING:'Climbing', RESUMED:'Started again', NEW_BEHAVIOR:'New behavior', PROVIDER_TARGETED:'One provider singled out', RATE_CUT:'Rate cut',
  NEW_REASON_CODE:'New reason code', DEADLINE:'Deadlines', STALE_FEED:'Feed stopped'};
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
  const can = a.family && a.family!=='feed' && a.family!=='rate' && a.family!=='reason_code';
  return `<div class="alert ${a.status}"><div class="row1"><span class="chip sev-${a.severity}">${a.severity}</span><span class="chip">${ALERT_KIND[a.kind]||a.kind}</span>
    <span class="muted small">first seen ${String(a.first_seen).slice(0,10)} · last checked ${String(a.last_seen).slice(0,10)}${a.status==='acknowledged'?' · acknowledged':''}${a.status==='cleared'?' · cleared':''}</span></div>
    <div class="ttl">${esc(a.title)}</div>${facts.length?`<div class="small muted">${facts.map(esc).join(' · ')}</div>`:''}
    <div class="actions" style="margin-top:6px">${can?`<button data-see="${a.alert_id}">See the lines</button>`:''}${a.status==='new'?`<button data-ack="${a.alert_id}">Acknowledge</button>`:''}</div></div>`;
}
function seeAlert(a){
  if(a.kind==='DEADLINE'){ state.tab='appeals'; state.filt.appeals={deadline:'due'}; return render(); }
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
  const dimOpts = sel => Object.keys(DIM_LABELS).map(k=>`<option value="${k}" ${sel===k?'selected':''}>${DIM_LABELS[k]}</option>`).join('');
  const chips = [['payer','Payer'],['provider','Provider'],['plan','Plan'],['code','Billed code'],['from','From'],['to','To']].filter(([k])=>ex[k])
    .map(([k,l])=>`<span class="chip">${l}: ${esc(k==='provider'?(state.provNames||{})[ex[k]]||ex[k]:ex[k])} <a data-clear="${k}" style="cursor:pointer">✕</a></span>`).join('');
  v.innerHTML = `<div class="panel"><h2>Who and what: ${FAMILY_LABELS[ex.family]}</h2>
    <div class="note">Every count sits next to the number of lines billed, so a busy provider or payer is not mistaken for a targeted one. "Per 100" is findings per 100 ${esc(res.rate_scope)}. Click a row to narrow everything to it, and the findings behind it are listed underneath.</div>
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
async function downcodeWho(el){
  const p = {family:'downcoding', status:statusScope(), year:state.year};
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
  v.innerHTML = `<div class="grid kpis">
    ${kpi('Identified', money(identified), 'downcoding, bundling, denials, underpayment', 'progress')}
    ${kpi('Recovered', money(g('resolved').recovered_cents), g('resolved').n.toLocaleString()+' findings paid after rework', 'progress')}
    ${kpi('Being worked', money(g('appealed').cents+g('in_progress').cents), (g('appealed').n+g('in_progress').n).toLocaleString()+' appealed or in progress', 'appeals')}
    ${kpi('Open and still appealable', money(p.live.cents), p.live.n.toLocaleString()+' findings inside the filing window', 'appeals')}
    ${kpi('Open, window passed', money(p.expired.cents), p.expired.n.toLocaleString()+' findings (deadlines are estimates)', 'appeals')}
    ${kpi('Lost', money(g('lost').cents), g('lost').n.toLocaleString()+' appeals upheld by the payer', 'progress')}</div>
    <div class="grid two">
      <div class="panel"><h2>Recovered by month</h2><div class="note">Money paid on a later remittance for lines that were first denied, bundled or cut. Counted automatically when the payment arrives.</div><canvas id="cRec"></canvas></div>
      <div class="panel"><h2>Share recovered, by month of the first payment</h2><div class="note">Of what went wrong in each month, how much has been won back so far. Recent months are still in play.</div><canvas id="cCoh"></canvas></div>
    </div>
    <div class="panel"><h2>Downcoded visits per 100, by payer</h2><div class="note">The fight in one chart. A payer's line going to zero means it stopped; a line coming back is what the alerts watch for.</div><canvas id="cDcRate"></canvas></div>
    <div class="panel"><h2>By payer</h2><table><thead><tr><th>Payer</th><th class="num">Findings</th><th class="num">Identified</th><th class="num">Recovered</th><th class="num">Paid later</th><th class="num">Avg days to payment</th><th class="num">Being worked</th><th class="num">Lost</th><th class="num">Open</th><th class="num">Still appealable</th></tr></thead>
      <tbody>${p.by_payer.map(r=>`<tr class="row" data-p="${esc(r.payer_group)}"><td>${esc(r.payer_group)}</td><td class="num">${r.n.toLocaleString()}</td><td class="num">${money(r.cents)}</td><td class="num">${money(r.recovered_cents)}</td><td class="num">${pct(r.resolved_n/r.n,0)}</td><td class="num">${r.avg_days==null?'–':r.avg_days}</td><td class="num">${r.working_n}</td><td class="num">${r.lost_n}</td><td class="num">${money(r.open_cents)}</td><td class="num">${money(r.live_cents)}</td></tr>`).join('')}</tbody></table></div>
    <div class="panel"><h2>Activity</h2><table class="small"><thead><tr><th>When</th><th>What</th><th>Payer</th><th>Finding</th><th class="num">Amount</th><th>By</th></tr></thead>
      <tbody>${p.events.map(e=>`<tr class="row" data-id="${e.finding_id}"><td>${String(e.at).replace('T',' ').slice(0,16)}</td><td>${esc(e.from_status||'')} → <b>${esc(e.to_status||'')}</b>${e.kind==='paid_on_later_remit'?' (paid on a later remit)':''}</td><td>${esc(e.payer_group||'')}</td><td>${esc(e.signal||'')} ${esc(e.procedure_code||'')}</td><td class="num">${money2(e.amount_cents)}</td><td>${esc(e.actor||'')}</td></tr>`).join('') || '<tr><td colspan="6" class="muted">No status changes yet. Mark findings appealed, resolved or lost from their detail panel and they show up here.</td></tr>'}</tbody></table></div>`;
  v.querySelectorAll('.kpi').forEach(k=>k.onclick=()=>{ if(k.dataset.go!=='progress'){ state.tab=k.dataset.go; render(); } });
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
  const fmtInt = n => Number(n).toLocaleString('en-US');
  const yes = (b, t, f) => b ? `<span class="ok">${t}</span>` : `<span class="sev-medium">${f}</span>`;
  el.innerHTML = `<div class="panel"><h2>Feed</h2><table>
    <tr><td>Dropbox connection</td><td>${yes(s.dropbox_configured,'connected','not configured: set DROPBOX_APP_KEY, DROPBOX_APP_SECRET and DROPBOX_REFRESH_TOKEN')}</td></tr>
    ${cloud ? `<tr><td>Automatic sync</td><td>${yes(s.cron_ready,'once a day (10:17 UTC, early morning Central), and when Dropbox reports a new file','off: CRON_SECRET is not set')} · <a href="/setup">setup checklist</a></td></tr>
    ${s.storage_error?`<tr><td>Storage</td><td><span class="sev-high">${esc(s.storage_error)}</span></td></tr>`:''}
    ${s.stalled_since?`<tr><td>Last run</td><td><span class="sev-high">A run that started ${esc(String(s.stalled_since).replace('T',' ').slice(0,16))} UTC stopped without finishing (out of memory or time). The next run picks up where it left off.</span></td></tr>`:''}
    ${mem.peak?`<tr><td>Memory used by the last run</td><td>${fmtInt(mem.peak)} MB at its peak${mem.limit?` of ${fmtInt(mem.limit)} MB available`:''}</td></tr>`:''}`
    : `<tr><td>Automatic sync</td><td>${s.interval_minutes?`every ${s.interval_minutes} minutes${s.next_run?', next about '+s.next_run.replace('T',' ').slice(0,16)+' UTC':''}`:'off (set REMITWATCH_SYNC_INTERVAL_MINUTES, for example 60)'} ${s.scheduler_running?'<span class="ok">· scheduler running</span>':''}</td></tr>`}
    <tr><td>Instant sync on upload</td><td>${yes(s.webhook_ready,'ready: point the Dropbox app webhook at /api/dropbox/webhook','needs DROPBOX_APP_SECRET')}</td></tr>
    <tr><td>Newest payment in the store</td><td>${esc(s.newest_payment_date||'–')} ${s.days_behind!=null?`(${s.days_behind} days ago)`:''}</td></tr>
    <tr><td>Last new remittance file</td><td>${(s.last_remit_ingested||'–').replace('T',' ').slice(0,16)}</td></tr>
    <tr><td>Last cycle</td><td>${lc.finished?`${lc.finished.replace('T',' ').slice(0,16)} UTC (${esc(lc.trigger||'')})${lc.error?' <span class="sev-high">failed: '+esc(lc.error)+'</span>':''} · ${lc.ingest?`${lc.ingest.remits||0} remits, ${lc.ingest.ema||0} EMA exports, ${lc.ingest.claims_837||0} claim files`:''}${lc.alerts?` · ${lc.alerts.new} new alerts`:''}`:'never'}</td></tr>
    <tr><td>Email (alerts and weekly summary)</td><td>${yes(s.smtp_configured,'configured','not configured: set SMTP_HOST and REPORT_TO')}</td></tr>
    <tr><td>Alert webhook</td><td>${s.alert_webhook?'<span class="ok">configured</span>':'off (optional: ALERT_WEBHOOK_URL)'}</td></tr>
    <tr><td>Clinical note review</td><td>${yes(s.note_review,'on','off: set ANTHROPIC_API_KEY once a business associate agreement covers it; packets are still built from the template without it')}</td></tr>
    </table><div class="actions" style="margin-top:8px"><button class="primary" id="syncNow" ${s.running?'disabled':''}>${s.running?'Running…':'Sync and analyse now'}</button><span class="small muted">Pulls new files from Dropbox, ingests them, re-runs detection and the alert checks. Safe to press at any time.</span></div></div>`;
  el.querySelector('#syncNow').onclick = async e => { e.target.disabled = true; e.target.textContent = 'Started…'; await post('/api/sync/run'); setTimeout(()=>{ refreshHeader(); feedPanel(el); }, 4000); };
}

// ---------------------------------------------------------------- finding detail: facts, history, note-based appeal builder
function drawerExtras(f, r){
  const facts = [['Billed code', f.billed_code], ['Billed → paid', f.code_pair], ['Plan on the remit', f.plan], ['Place of service', f.pos_class], ['Location', f.location], ['Diagnosis', f.diagnosis],
    ['Modifier', f.modifier_class], ['Recovered', f.recovered_cents!=null ? money2(f.recovered_cents)+' on '+(f.recovered_date||'') : null], ['Appealed', f.appealed_at ? String(f.appealed_at).slice(0,10) : null]].filter(x=>x[1]!=null && x[1]!=='');
  return `${f.stale?'<div class="note sev-medium">The current rules no longer produce this finding. It is kept because someone worked it.</div>':''}
    <table class="small">${facts.map(([k,val])=>`<tr><td>${k}</td><td>${esc(val)}</td></tr>`).join('')}
      <tr><td>Filing window</td><td>${f.appeal_deadline?'by <b>'+esc(f.appeal_deadline)+'</b>. ':''}${esc(f.deadline_basis||'')}</td></tr></table>
    <div class="panel" style="margin:10px 0"><h2>Build the appeal from the visit note</h2>
      <div class="note">Attach the note for this date of service (PDF, Word or text) or paste it. The review checks the note against the CPT rules that decide this dispute, quotes it word for word, and tells you plainly when the note does not support an appeal. The result is one PDF: letter, review, remittance evidence and the note.</div>
      <div class="filters"><input type="file" id="noteFile" accept=".pdf,.docx,.txt,.rtf,.html,.htm"><label class="small"><input type="checkbox" id="noteAi" checked> review with Claude</label><button class="primary" id="noteGo">Build packet</button></div>
      <textarea id="noteText" rows="4" placeholder="…or paste the note text here" style="width:100%"></textarea>
      <div id="noteOut">${(r.packets||[]).length?`<div class="small"><b>Earlier packets:</b> ${r.packets.map(p=>`<a href="/api/packets/${p.packet_id}/pdf">${String(p.created_at).slice(0,10)} ${esc(p.note_name||'')}</a>${p.emailed_to?' (emailed to '+esc(p.emailed_to)+')':''}`).join(' · ')}</div>`:''}</div></div>
    ${(r.events||[]).length?`<h3 class="small">History</h3><table class="small"><tbody>${r.events.map(e=>`<tr><td>${String(e.at).replace('T',' ').slice(0,16)}</td><td>${esc(e.from_status||'')} → <b>${esc(e.to_status||'')}</b></td><td>${money2(e.amount_cents)}</td><td>${esc(e.actor||'')}</td><td>${esc(e.note||'')}</td></tr>`).join('')}</tbody></table>`:''}`;
}
const REC_LABEL = {appeal:['Appeal','ok'], appeal_with_caution:['Appeal with caution','sev-medium'], do_not_appeal:['Do not appeal','sev-high'], correct_and_resubmit:['Correct and resubmit','sev-medium'], review_manually:['Review by hand','muted']};
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
    <div class="note">The PDF contains the patient's name, member ID and the note. Email it only to a mailbox inside the practice or a payer address that accepts appeals.</div>
    ${p.corrected_claim_instructions?`<div class="small"><b>Correction instead of appeal:</b> ${esc(p.corrected_claim_instructions)}</div>`:''}
    <pre>${esc(p.letter)}</pre></div>`;
}
function bindDrawerExtras(id, d){
  const out = d.querySelector('#noteOut'); if(!out) return;
  d.querySelector('#noteGo').onclick = async () => {
    const file = d.querySelector('#noteFile').files[0], text = d.querySelector('#noteText').value;
    if(!file && !text.trim()){ out.innerHTML = '<div class="sev-medium small">Attach the note or paste its text first.</div>'; return; }
    const fd = new FormData(); fd.append('finding_id', id); fd.append('text', text); fd.append('use_ai', d.querySelector('#noteAi').checked ? '1' : '0'); if(file) fd.append('file', file);
    out.innerHTML = '<div class="muted small">Reading the note and building the packet. A review takes about a minute.</div>';
    try {
      const p = await api('/api/appeals/build', {method:'POST', body: fd});
      out.innerHTML = packetHtml(p);
      out.querySelector('#pkCopy').onclick = () => navigator.clipboard.writeText(p.letter);
      out.querySelector('#pkSend').onclick = async () => { const res = await post('/api/packets/'+p.packet_id+'/email', {to: out.querySelector('#pkTo').value}); out.querySelector('#pkMsg').textContent = res.sent ? 'sent to '+res.to : 'not sent: '+res.reason; };
    } catch(e){ out.innerHTML = `<div class="sev-high small">Could not build the packet: ${esc(e.message)}</div>`; }
  };
}
async function setFindingStatus(id, status, shortfall){
  const body = {status};
  if(status==='resolved'){ const x = prompt('Amount recovered, in dollars. Leave blank to use the shortfall.', shortfall!=null ? (shortfall/100).toFixed(2) : ''); if(x===null) return false; if(x.trim()!=='' && !isNaN(parseFloat(x))) body.recovered_cents = Math.round(parseFloat(x)*100); }
  if(status==='lost'){ const n = prompt('Why was it lost? (optional note)'); if(n===null) return false; if(n) body.notes = n; }
  await post('/api/findings/'+id+'/status', body); return true;
}
