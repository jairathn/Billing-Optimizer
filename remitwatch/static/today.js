// RemitWatch daily screens: Today (what to do now), Bigger wins (money fixed once, collected on every future claim), More
// (the analysis pages), the first-visit guide and the What to do box of an item. The category pages and the Worklist are in
// desk.js. Loaded after features.js and before the page's own script; uses its helpers (api, esc, money, money2, state,
// render, openDetail, setFindingStatus).

const KIND_STYLE = {fix:'k-fix', auth:'k-auth', note:'k-note', rule:'k-rule', rate:'k-rate', check:'k-check'};
const STATUS_WORDS = {open:'To do', in_progress:'Started', appealed:'Sent', resolved:'Paid', lost:'Lost', dismissed:'Let go'};
const OWNER_OF = {fix:'Billing', auth:'Front desk', note:'Clinicians and coder', rule:'Billing', rate:'Billing', check:'Front desk and billing'};
const GUIDE_KEY = 'rw:guide:v1';

function guideSeen(){ try { return localStorage.getItem(GUIDE_KEY) === '1'; } catch(e){ return false; } }
function guideDone(){ try { localStorage.setItem(GUIDE_KEY, '1'); } catch(e){} }
function guideReset(){ try { localStorage.removeItem(GUIDE_KEY); } catch(e){} }

async function brief(force){
  if(force || !state.brief || Date.now() - state.briefAt > 60000){
    state.brief = await api('/api/brief'); state.briefAt = Date.now();
    if(typeof updateBadges === 'function') updateBadges(state.brief);
  }
  return state.brief;
}
function kindOf(b, k){ return (b.work_types||[]).find(t=>t.key===k) || {key:k, label:k, how:'', needs:''}; }
function labelOf(b, sig){ return (b.labels||{})[sig] || {label: sig, next: '', work: null}; }
function daysUntil(d){ if(!d) return null; const t = new Date(d+'T00:00:00Z'), n = new Date(new Date().toISOString().slice(0,10)+'T00:00:00Z'); return Math.round((t-n)/86400000); }
function closesText(d){
  const n = daysUntil(d);
  if(n==null) return {text:'no date', cls:'muted'};
  if(n < 0) return {text: n===-1 ? 'passed yesterday' : `passed ${-n} days ago`, cls:'muted'};
  if(n === 0) return {text:'closes today', cls:'due-now'};
  if(n === 1) return {text:'tomorrow', cls:'due-now'};
  return {text:`in ${n} days`, cls: n < 7 ? 'due-now' : (n < 30 ? 'due-soon' : '')};
}
function rwMonth(m){ const x = new Date((m||'')+'-15T00:00:00Z'); return isNaN(x) ? esc(m||'') : x.toLocaleDateString('en-US', {month:'long', year:'numeric', timeZone:'UTC'}); }
function longDate(d){ const x = new Date((d||'')+'T00:00:00Z'); return isNaN(x) ? esc(d||'') : x.toLocaleDateString('en-US', {month:'short', day:'numeric', year:'numeric', timeZone:'UTC'}); }
function goWork(filt){ state.tab = 'worklist'; location.hash = 'worklist'; state.filt.worklist = Object.assign({status:'open,in_progress', deadline:'valid'}, filt||{}); state.page.worklist = 0; if(state.lim) state.lim.worklist = 0; render(); }
// the kind of work an item takes, as insights.work_type decides it: a whole claim denied goes by its action and reason
function kindFor(b, r){
  if(r.signal === 'CLAIM_DENIED_UNPAID'){
    if(r.actionable === 'correct_and_resubmit') return 'fix';
    const ck = b.claim_kinds || {};
    return Object.keys(ck).find(k => (ck[k]||[]).includes(String(r.carc||''))) || 'check';
  }
  const lb = labelOf(b, r.signal);
  return lb.work || ({correct_and_resubmit:'fix', appeal:'rule'}[r.actionable] || 'check');
}
const pl = (n, one, many) => `${Number(n||0).toLocaleString()} ${Number(n) === 1 ? one : (many || one + 's')}`;
function kindBadge(b, k){ return `<span class="kind ${KIND_STYLE[k]||''}">${esc(kindOf(b,k).label)}</span>`; }

// ---------------------------------------------------------------- the first-visit guide
function guideHtml(b){
  const w = (b && b.work && b.work.total) || {};
  return `<div class="guide" id="guide">
    <div class="guide-head"><h2>How RemitWatch works</h2><button class="ghost" id="guideClose" aria-label="Close the guide">✕</button></div>
    <p class="lead">RemitWatch reads every payment your payers send and lists the money they kept that you can still get back, with what to do about each item and the date it must be done by.</p>
    <ol class="steps">
      <li><b>Start with Today's plan.</b> Step 1 is always what closes soonest. ${w.n ? `Right now ${Number(w.n).toLocaleString()} items worth ${money(w.cents)} are still inside their filing windows.` : ''}</li>
      <li><b>Open an item.</b> The box at the top says what happened and what to do. Do it in EMA or the payer's portal, then press <b>Mark sent</b>. The next item opens.</li>
      <li><b>When the payer pays,</b> RemitWatch sees it in the next remittance and marks the item Paid by itself. If the payer says no, press <b>Lost</b>.</li>
    </ol>
    <div class="guide-more"><b>Worklist</b> has every item, sorted by what kind of work it takes. <b>Bigger wins</b> is money you fix once and collect on every future claim: contracts, your fees, payment fees and denial causes. <b>Data</b> tells you when the numbers cannot be trusted yet.</div>
    <div class="actions"><button class="primary" id="guideGo">Show me the first item</button><button id="guideOk">Got it</button></div>
  </div>`;
}
function bindGuide(v){
  const g = v.querySelector('#guide'); if(!g) return;
  const close = () => { guideDone(); g.remove(); };
  g.querySelector('#guideClose').onclick = close;
  g.querySelector('#guideOk').onclick = close;
  g.querySelector('#guideGo').onclick = () => { guideDone(); goWork({deadline:'valid', sort:'deadline', first:'1'}); };
}
function openGuide(){ guideReset(); if(state.tab !== 'today'){ state.tab = 'today'; location.hash = 'today'; } render(); }

// ---------------------------------------------------------------- Today
async function todayTab(v){
  const b = await brief(true);
  if(!b.ready){ v.innerHTML = '<div class="panel"><h2>Nothing analysed yet</h2><div class="note">The first analysis run has not finished. Open Data and press Sync and analyse now.</div></div>'; return; }
  const w = b.work.total, bad = (b.health||[]).filter(h=>!h.ok);
  const top = b.batches[0], dec = b.decide, fu = b.follow_ups, done = b.done || {};
  const day = new Date().toLocaleDateString('en-US', {weekday:'long', month:'long', day:'numeric'});
  const byKind7 = (b.work.types||[]).filter(t=>t.due7_n).sort((x,y)=>y.due7_cents-x.due7_cents);
  const steps = [];
  steps.push(w.due7_n ? {n:1, title:'Work what closes this week', line:`${Number(w.due7_n).toLocaleString()} items worth ${money(w.due7_cents)} have a filing window that closes within 7 days.`,
      chips: byKind7.map(t=>`${kindBadge(b,t.key)} ${t.due7_n}`).join(' '), btn:`Start (${w.due7_n})`, go:{deadline:'week', sort:'deadline'}}
    : {n:1, title:'Nothing closes this week', line: w.due30_n ? `${w.due30_n} items worth ${money(w.due30_cents)} close within 30 days.` : 'No filing window closes in the next 30 days.', done:true, btn: w.due30_n ? 'See the next 30 days' : '', go:{deadline:'month', sort:'deadline'}});
  if(fu && fu.n) steps.push({n:steps.length+1, title:'Follow up on what you sent', line:`${fu.n} items sent to the payer more than ${fu.days} days ago are still unpaid (${money(fu.cents)}). Check the portal or call.`, btn:`Follow up (${fu.n})`, go:{status:'appealed', deadline:'followup', sort:'deadline'}});
  if(top) steps.push({n:steps.length+1, title:'Clear the biggest batch', line:`${esc(kindOf(b,top.kind).label)}, ${esc(top.payer_group)}: ${top.n} items on ${top.claims} claims, ${money(top.cents)}${top.top_code?`, mostly ${esc(top.top_code)}`:''}. The first window ${closesText(top.first_deadline).text === 'closes today' ? 'closes today' : 'closes '+closesText(top.first_deadline).text}.`,
      chips: kindBadge(b, top.kind), btn:'Open the batch', go:{kind: top.kind, payer: top.payer_group, sort:'deadline'}});
  const older_n = Math.max(0, (b.work.undated_n||0) - (dec ? dec.n : 0)), older_c = Math.max(0, (b.work.undated_cents||0) - (dec ? dec.cents : 0));
  if(dec && dec.n){ const g = dec.groups[0]; steps.push({n:steps.length+1, title:'Decide on new items with no filing date', line:`${dec.n} items worth ${money(dec.cents)} paid in the last ${dec.days||120} days need a person to say what they are (bill another payer or the patient, appeal, or let go).${g?` Biggest: ${esc(labelOf(b,g.signal).label.toLowerCase())}, ${esc(g.payer_group)} (${g.n}, ${money(g.cents)}).`:''}${older_n?` <a href="#" class="see-undated">${pl(older_n, 'older one')} (${money(older_c)})</a> ${older_n===1?'has':'have'} no filing date either.`:''}`, btn:`Look at them (${dec.n})`, go:{deadline:'decide', status:'open,in_progress', sort:'shortfall'}}); }
  else if(older_n) steps.push({n:steps.length+1, title:'Items with no filing date', line:`${pl(older_n, 'item')} worth ${money(older_c)} ${older_n===1?'has':'have'} no filing date: a person has to say what they are.`, btn:'Look at them', go:{deadline:'older', status:'open,in_progress', sort:'shortfall'}});
  const alerts = (state.alertsNew||[]).filter(a=>!['DEADLINE','STALE_FEED','PROVIDER_TARGETED'].includes(a.kind) && a.severity!=='info').slice(0,3);
  const missed = b.missed || {};
  v.innerHTML = `${guideSeen() ? '' : guideHtml(b)}
    ${bad.length ? `<div class="banner"><b>Before you start:</b> ${esc(bad[0].title)}. ${esc(bad[0].detail||'')} <a href="#data" class="go-data">How to fix</a>${bad.length>1?` · <a href="#data" class="go-data">${bad.length-1} more data ${bad.length-1===1?'check':'checks'}</a>`:''}</div>` : ''}
    <div class="today-head"><div><div class="muted small">${esc(day)}</div><h1 class="page-title">Today's plan</h1></div>
      <div class="donebox"><span>Moved on today <b>${done.today_n||0}</b></span><span>This week <b>${pl(done.week_n, 'item')}</b>, ${money(done.week_cents||0)} sent or paid</span>${(b.recovered||{}).year_cents ? `<span>Paid back in ${esc(b.recovered.year)} <b>${money(b.recovered.year_cents)}</b></span>` : ''}</div></div>
    <div class="plan">${steps.map(s=>`<div class="step ${s.done?'is-done':''}"><div class="num">${s.done?'✓':s.n}</div><div class="body"><div class="title">${esc(s.title)}</div><div class="line">${s.line}</div>${s.chips?`<div class="chips">${s.chips}</div>`:''}</div>${s.btn?`<button class="${s.done?'':'primary'}" data-step="${s.n}">${esc(s.btn)}</button>`:''}</div>`).join('')}</div>
    <div class="panel"><div class="panel-head"><h2>Batches</h2><span class="muted small">One kind of work for one payer, so one person can clear a pile in one sitting. ${b.batches.length} batches, ${money(w.cents)} inside a filing window.${smallLine(b)}</span></div>
      <div class="batches" id="batchList">${batchRows(b, b.batches.slice(0, 8))}</div>
      ${b.batches.length > 8 ? `<button class="ghost" id="allBatches">Show all ${b.batches.length} batches</button>` : ''}</div>
    ${alerts.length ? `<div class="panel"><div class="panel-head"><h2>What changed</h2><span class="muted small">Payer behaviour that moved recently.</span></div>${alerts.map(a=>`<div class="change"><span class="dot sev-${esc(a.severity)}">●</span><div>${esc(a.title)}</div><button class="ghost seeA" data-id="${esc(a.alert_id)}">Look into it</button></div>`).join('')}<a href="#alerts" class="small">All alerts</a></div>` : ''}
    <div class="panel wins-teaser"><div class="panel-head"><h2>Bigger wins</h2><span class="muted small">Fixed once, collected on every future claim.</span></div>${winsTeaser(b)}<button id="goWins">Open Bigger wins</button></div>
    ${missed.n ? `<div class="missed small muted">Missed in the last ${missed.days} days: ${Number(missed.n).toLocaleString()} items worth ${money(missed.cents)} passed their filing window without anyone working them. <a href="#" id="seeMissed">See them</a></div>` : ''}`;
  bindGuide(v);
  v.querySelectorAll('[data-step]').forEach(bt=>bt.onclick=()=>{ const s = steps.find(x=>String(x.n)===bt.dataset.step); if(s) goWork(s.go); });
  bindBatches(v, b);
  const all = v.querySelector('#allBatches'); if(all) all.onclick = () => { v.querySelector('#batchList').innerHTML = batchRows(b, b.batches); bindBatches(v, b); all.remove(); };
  v.querySelectorAll('.go-data').forEach(a=>a.onclick=e=>{ e.preventDefault(); state.tab='data'; location.hash='data'; render(); });
  v.querySelectorAll('.seeA').forEach(bt=>bt.onclick=()=>{ const a = alerts.find(x=>x.alert_id===bt.dataset.id); if(a && typeof seeAlert==='function') seeAlert(a); });
  const gw = v.querySelector('#goWins'); if(gw) gw.onclick = () => { state.tab='wins'; location.hash='wins'; render(); };
  const sm = v.querySelector('#seeMissed'); if(sm) sm.onclick = e => { e.preventDefault(); goWork({deadline:'missed', status:'open', sort:'deadline'}); };
  v.querySelectorAll('.see-undated').forEach(a=>a.onclick = e => { e.preventDefault(); goWork({deadline:'older', status:'open,in_progress', sort:'shortfall'}); });
}
function smallLine(b){
  const w = b.work || {}, n = w.small_n || 0; if(!n) return '';
  const pct = Math.round(100 * (w.small_cents || 0) / Math.max(1, (w.total || {}).cents || 0));
  return ` ${Number(n).toLocaleString()} of the items are worth less than ${money(w.small_under || 2000)} each (${money(w.small_cents)} together, ${pct}% of the money): the Worklist's amount filter hides them.`;
}
function batchRows(b, rows){
  if(!rows.length) return '<div class="muted">No open work inside a filing window.</div>';
  return rows.map((x,i)=>{ const k = kindOf(b, x.kind), c = closesText(x.first_deadline), done = x.started_n + x.sent_n;
    return `<div class="batch"><div class="b-main">${kindBadge(b, x.kind)} <b>${esc(x.payer_group)}</b>
        <div class="small muted">You need: ${esc(k.needs||'')}${x.top_code?` · mostly ${esc(x.top_code)}`:''}</div></div>
      <div class="b-num"><b>${money(x.cents)}</b><div class="small muted">${pl(x.n, 'item')} · ${pl(x.claims, 'claim')}</div></div>
      <div class="b-due"><span class="${c.cls}">${esc(c.text === 'closes today' ? 'first one closes today' : 'first one closes ' + c.text)}</span><div class="small muted">${x.due14_n} ${x.due14_n===1?'closes':'close'} within 14 days</div></div>
      <div class="b-prog small muted">${done ? [x.started_n ? `${x.started_n} started` : '', x.sent_n ? `${x.sent_n} sent` : ''].filter(Boolean).join(' · ') : 'not started'}</div>
      <button data-batch="${i}">Start</button></div>`; }).join('');
}
function bindBatches(v, b){ v.querySelectorAll('[data-batch]').forEach(bt=>bt.onclick=()=>{ const x = b.batches[Number(bt.dataset.batch)]; if(x) goWork({kind:x.kind, payer:x.payer_group, sort:'deadline'}); }); }
function winsTeaser(b){
  const c = (b.contracts.payers||[]).filter(p=>p.gap_cents>0), f = b.fees || {};
  const parts = [];
  if(c.length) parts.push(`<li><b>Contracts:</b> ${esc(c[0].payer_group)} paid about ${Math.round(c[0].pct_of_medicare*100)}% of Medicare in ${esc(b.contracts.year)}, ${money(c[0].gap_cents)} a year below Medicare${c.length>1?`, and ${c.length-1} more ${c.length-1===1?'payer':'payers'}`:''}.</li>`);
  if(f.payment_fee_cents) parts.push(`<li><b>Payment fees:</b> ${money(f.payment_fee_cents)} taken out of payments by payment vendors${f.payment_fee_since?' since '+rwMonth(f.payment_fee_since):''}.</li>`);
  if(f.charge_cap_cents) parts.push(`<li><b>Your fees:</b> ${f.charge_caps.length} ${f.charge_caps.length===1?'code is':'codes are'} billed below what a payer allows, ${money(f.charge_cap_cents)} left unpaid so far${f.charge_caps[0]&&f.charge_caps[0].top_rate_cents?` (${esc(f.charge_caps[0].code)}: your fee ${money2(f.charge_caps[0].charge_cents)}, a payer allows ${money2(f.charge_caps[0].top_rate_cents)})`:''}.</li>`);
  parts.push(`<li><b>January 1, 2027:</b> when a modifier-25 visit and a 0-day procedure fall on the same day, Medicare pays the most expensive service in full and every other service that day at 80%; G2211 becomes modifier B1.</li>`);
  return `<ul class="wins-list">${parts.join('')}</ul>`;
}

// ---------------------------------------------------------------- Worklist
const WL_DEADLINES = [['valid','Window still open'],['week','Closes within 7 days'],['month','Closes within 30 days'],['decide','To decide (no filing date, paid in the last 120 days)'],['older','No filing date, paid before that'],
  ['followup','Sent over 30 days ago, not paid'],['missed','Window closed in the last 30 days'],['none','No filing date'],['expired','Window closed'],['','Any date']];
const WL_STATUSES = [['open,in_progress','To do and started'],['open','To do'],['in_progress','Started'],['appealed','Sent'],['resolved','Paid'],['lost','Lost'],['dismissed','Let go'],['all','Everything']];
const MONEY_CATS = 'denial,bundling,downcoding,underpayment';
// the "What to do" box at the top of an item, and stepping through the list
function whatToDoHtml(f, letter){
  const b = state.brief; if(!b || !b.labels) return '';
  const lb = labelOf(b, f.signal), kk = kindFor(b, f), k = kindOf(b, kk), est = /not confirmed/.test(f.deadline_basis||'');
  const ev = (f.evidence && typeof f.evidence === 'object') ? f.evidence : {}, today = new Date().toISOString().slice(0,10);
  const open = (ev.deadlines||[]).filter(x=>Array.isArray(x) && x[0] && x[0] >= today).map(x=>x[0]).sort();
  const due = open[0] || f.appeal_deadline, c = closesText(due);
  const next = f.next_step || ((f.signal === 'CLAIM_DENIED_UNPAID' && kk !== 'check') ? k.how : lb.next);
  const openCents = (ev.deadlines||[]).filter(x=>Array.isArray(x) && x[0] && x[0] >= today).reduce((s,x)=>s+(x[2]||0), 0);
  const stake = (ev.deadlines||[]).length ? `${money2(openCents)} at stake on the claims still inside their windows (of ${money2(f.shortfall_cents)} in all).`
    : (f.shortfall_cents ? money2(f.shortfall_cents)+' at stake.' : 'The amount is not known: this payer has no usual rate for the code to compare with.');
  const ids = state.worklist||[], i = ids.indexOf(f.finding_id);
  const more = i>=0 && (i < ids.length-1 || (!!state.wlNext && (state.wlTotal||0) > ids.length)), check = kk === 'check' || !due;
  const waiting = {appealed:`Sent${f.appealed_at?' on '+longDate(String(f.appealed_at).slice(0,10)):''}. It waits on the payer: if it is still unpaid 30 days after sending, follow up by portal or phone.`,
    resolved:'Paid. Nothing more to do.', lost:'Lost: the payer upheld its decision.', dismissed:'Let go.'}[f.status];
  const shut = !waiting && !!due && due < today;        // a closed window: nothing to send, so no send button and no letter
  return `<div class="todo-box"><div class="todo-top">${kindBadge(b, kk)}<span class="${c.cls}">${due ? `File by ${longDate(due)} (${esc(c.text)})${est?', estimated rule':''}${open.length>1?`; its last claim by ${longDate(f.appeal_deadline)}`:''}` : 'No filing date'}</span>
      ${i>=0 ? `<span class="muted small">Item ${i+1} of ${Number(Math.max(state.wlTotal||0, ids.length)).toLocaleString()}</span>` : ''}</div>
    <div class="todo-what">${stake}</div>
    ${f.carc_text ? `<div class="small muted">Payer's reason: ${esc(f.carc)} ${esc(f.carc_text)}${f.rarc_text?' · '+esc(f.rarc)+' '+esc(f.rarc_text):''}</div>` : ''}
    ${shut ? `<div class="todo-next">The filing window closed on ${longDate(due)}. Nothing to send: keep it for the record, or mark it Lost or Let go.</div>` : waiting ? `<div class="todo-next"><b>Status:</b> ${esc(waiting)}</div>` : `<div class="todo-next"><b>What to do:</b> ${esc(next)}</div>
    <div class="small muted">You need: ${esc(k.needs||'')}</div>`}
    <div class="todo-err sev-high small" id="todoErr" role="alert"></div>
    <div class="actions todo-actions">${more && !waiting && !shut ? `<button class="primary" id="sentNext" data-to="${check ? 'in_progress' : 'appealed'}">${check ? 'Mark started and open the next' : 'Mark sent and open the next'}</button>` : ''}
      ${shut ? '' : (letter||'')}${i>0 ? '<button class="ghost" id="prevItem">‹ Previous</button>' : ''}${more ? '<button class="ghost" id="nextItem">Next ›</button>' : ''}</div></div>`;
}
function bindWhatToDo(f, d){
  const ids = state.worklist||[], i = ids.indexOf(f.finding_id);
  // the next item: from the rows shown, or the next rows of the same list, fetched at the end of them
  const next = async () => {
    if(i >= 0 && i < (state.worklist||[]).length - 1) return state.worklist[i+1];
    if(state.wlNext){ try { await state.wlNext(); } catch(e){ return null; } }
    return (state.worklist||[])[i+1] || null;
  };
  const go = async () => { const id = await next(); if(id) openDetail(id); else closeDetail(); };
  const sn = d.querySelector('#sentNext'); if(sn) sn.onclick = async () => {
    sn.disabled = true;
    try { await post('/api/findings/'+f.finding_id+'/status', {status: sn.dataset.to || 'appealed'}); }
    catch(e){ sn.disabled = false; const er = d.querySelector('#todoErr'); if(er) er.textContent = 'Not saved: ' + (e.message || 'no answer from the site') + '. Try again.'; return; }
    state.brief = null; state.dirty = true; go(); };
  const pv = d.querySelector('#prevItem'); if(pv) pv.onclick = () => openDetail(ids[i-1]);
  const nx = d.querySelector('#nextItem'); if(nx) nx.onclick = () => go();
}

// ---------------------------------------------------------------- Bigger wins
async function winsTab(v){
  const b = await brief();
  const c = b.contracts || {payers:[]}, f = b.fees || {}, causes = (b.causes||[]).slice(0, 10);
  const trend = x => !x.n_prev ? '' : (x.cents90 > x.cents_prev * 1.15 ? '<span class="sev-high">rising</span>' : (x.cents90 < x.cents_prev * 0.85 ? '<span class="ok">falling</span>' : '<span class="muted">steady</span>'));
  const why = text => `<details class="why-fold"><summary>Why</summary><div class="note">${text}</div></details>`;
  v.innerHTML = `<div class="page-head"><h1 class="page-title">Bigger wins</h1><span class="muted small">Money you fix once and collect on every future claim. None of it is appealed claim by claim, so it never shows up on Today.</span></div>
    <div class="grid two">
      <div class="panel" id="wins-rates"><h2>Raise the rates</h2><div class="small"><b>Next step:</b> get the signed fee schedule for the payers at the top and bring this to the contract talk.</div>
        ${why(`What each commercial and Medicare Advantage payer allowed for office services in ${esc(c.year||'the last full year')}, as a share of what Medicare paid you for the same codes and volume (100% is Medicare's rate; "office visits" is the 99202 to 99215 codes alone). Below Medicare is a contract question, not an error the site can appeal.`)}
        <table><thead><tr><th>Payer</th><th class="num">All office services</th><th class="num">Office visits</th><th class="num">A year below Medicare</th></tr></thead>
        <tbody>${(c.payers||[]).slice(0,8).map(p=>`<tr><td>${esc(p.payer_group)}<div class="small muted">${esc(p.payer_kind==='medicare_advantage'?'Medicare Advantage':'commercial')} · ${Number(p.lines).toLocaleString()} lines</div></td><td class="num ${p.pct_of_medicare<0.9?'sev-high':(p.pct_of_medicare<1?'sev-medium':'ok')}">${Math.round(p.pct_of_medicare*100)}%</td><td class="num">${p.visit_pct_of_medicare!=null?Math.round(p.visit_pct_of_medicare*100)+'%':'–'}</td><td class="num"><b>${p.gap_cents>0?money(p.gap_cents):'–'}</b></td></tr>`).join('')}</tbody></table>
        <a href="#contracts" class="small" id="ratesLink">Rates by code and payer</a></div>
      <div class="panel" id="wins-2027"><h2>What changes on January 1, 2027</h2>
        <ul class="wins-list"><li><b>Same-day visits:</b> a modifier-25 visit with a 0-day procedure: the most expensive service is paid in full, every other service that day at 80%.</li>
          <li><b>G2211 ends:</b> it becomes modifier B1 on the visit itself. Change the charge setup in EMA before January.</li>
          <li><b>Rates fall</b> about 4 to 6% on common dermatology codes (99213 in Kentucky goes from $89.52 to $85.54).</li>
          <li><b>Indiana Medicaid</b> moves to 100% of the January 2026 Medicare rates, and its managed-care plans must match.</li></ul>
        ${why(`From Medicare's 2027 physician fee schedule (final rule, November 2026). Same-day visits: biopsy, shave, injection and Mohs are 0-day procedures. When one is done with a modifier-25 visit, every other service that day is paid at 80%, a destruction or excision on the same claim included; a visit with only a destruction (17000, 17110) or an excision, which have 10- or 90-day globals, does not bring the rule in. G2211: without the change, those add-on payments will be denied. Rates: the conversion factor drops 2.3% and Kentucky's and Indiana's work adjustment loses its floor. The site already expects the 80% on those lines from January, for Medicare and for Medicare Advantage plans that follow it, so they are not shown as underpaid. How it combines with the multiple-procedure cut on a second procedure awaits CMS's instructions, so those lines are not flagged meanwhile.`)}</div>
    </div>
    <div class="panel" id="wins-leaks"><h2>Stop the leaks</h2><div class="small"><b>Next step:</b> give each cause to the person named; it is a habit or a front-desk step to change.</div>
      ${why('Denial causes in the last 90 days of payments against the 90 days before. Appealing them one by one never ends; fixing the cause stops the next ones.')}
      <table><thead><tr><th>Cause</th><th>Who fixes it</th><th class="num">Last 90 days</th><th class="num">Items</th><th>Trend</th><th></th></tr></thead>
      <tbody>${causes.map(x=>`<tr><td>${esc(x.label)}</td><td class="small">${esc(OWNER_OF[x.work]||'Billing')}</td><td class="num"><b>${money(x.cents90)}</b></td><td class="num">${Number(x.n90).toLocaleString()}</td><td class="small">${trend(x)}</td><td><button class="ghost" data-cause="${esc(x.signal)}">See them</button></td></tr>`).join('')}</tbody></table></div>
    <div class="grid two">
      <div class="panel" id="wins-fees"><h2>Your fees</h2><div class="small"><b>Next step:</b> set each fee above the amount a payer allows; every later claim is then paid at the payer's rate.</div>
        ${why('Codes billed below what a payer allows: payers pay the lesser of your charge and their rate, so they paid exactly your charge. Nothing to appeal.')}
        ${(f.charge_caps||[]).length ? `<table><thead><tr><th>Code</th><th class="num">Your fee</th><th class="num">A payer allows</th><th class="num">Left unpaid</th><th>Payers</th></tr></thead><tbody>${f.charge_caps.slice(0,10).map(x=>`<tr><td><b>${esc(x.code)}</b><div class="small muted">${esc(x.first_year===x.year?x.year:x.first_year+' to '+x.year)}</div></td><td class="num">${money2(x.charge_cents)}</td><td class="num">${money2(x.top_rate_cents)}</td><td class="num">${money(x.cents)}<div class="small muted">${Number(x.lines).toLocaleString()} lines</div></td><td class="small">${x.payers.map(esc).join(', ')}</td></tr>`).join('')}</tbody></table><div class="small muted">Fee and allowed amount are from ${esc((f.charge_caps[0]||{}).year||'the newest year')} or the code's newest year.</div>` : '<div class="muted small">No code is capped by your charge.</div>'}</div>
      <div class="panel" id="wins-payfees"><h2>Payment fees</h2><div class="small"><b>Next step:</b> ask each payer or its vendor for standard direct deposit (ACH) with no fee.</div>
        ${why(`Fees payment vendors (Zelis and others) took out of payments${f.payment_fee_since?' since '+rwMonth(f.payment_fee_since):''}: ${money(f.payment_fee_cents||0)} in all.`)}
        ${(f.payment_fees||[]).length ? `<table><thead><tr><th>Payer</th><th class="num">Months with a fee</th><th class="num">Fees</th></tr></thead><tbody>${f.payment_fees.slice(0,8).map(x=>`<tr><td>${esc(x.payer_group)}<div class="small muted">${rwMonth(x.first_month)} to ${rwMonth(x.last_month)}</div></td><td class="num">${x.months}</td><td class="num">${money(x.cents)}</td></tr>`).join('')}</tbody></table>${f.payment_fees.length>8?`<div class="small muted">and ${f.payment_fees.length-8} smaller payers; <a href="#fees" id="feesLink">all of them on Fees & takebacks</a></div>`:''}` : '<div class="muted small">No payment fees found.</div>'}</div>
    </div>`;
  v.querySelectorAll('[data-cause]').forEach(bt=>bt.onclick=()=>goWork({signal: bt.dataset.cause, paid:'90', status:'all', deadline:'', sort:'shortfall'}));
  const rl = v.querySelector('#ratesLink'); if(rl) rl.onclick = e => { e.preventDefault(); goDetails('contracts'); };
  const fl = v.querySelector('#feesLink'); if(fl) fl.onclick = e => { e.preventDefault(); state.filt.feesDesk = {list:'fees'}; state.tab = 'fees'; location.hash = 'fees'; render(); };
  if(state.winsAnchor){ const el = v.querySelector('#'+state.winsAnchor); state.winsAnchor = null; if(el) el.scrollIntoView({block:'start'}); }
}

// ---------------------------------------------------------------- More: the analysis pages
const MORE_PAGES = [
  ['overview','Overview','Every finding by category, month and payer, including money past its filing window.'],
  ['explorer','Who & what','Count any finding by payer, clinician, code, plan or month.'],
  ['payers','Payers & rates','Findings and dollars by payer and by clinician.'],
  ['alerts','Alerts','Payers behaving differently from their own past, new and acknowledged.'],
  ['progress','Progress','What came back after a fix or appeal, and what is being worked.', 'mid-only'],
  ['data','Data','Whether the numbers can be trusted, and who fixes what.', 'mid-only'],
  ['providers','Providers','What each clinician is doing, behind the owner\'s password where one is set.', 'mid-only'],
];
async function moreTab(v){
  v.innerHTML = `<div class="page-head"><h1 class="page-title">More</h1><span class="muted small">Pages for questions and for the owner. The daily work is on Today, the category pages and the Worklist.</span></div>
    <div class="more-grid">${MORE_PAGES.map(([k,t,d,cls])=>`<a class="more-card ${cls||''}" href="#${k}" data-go="${k}"><b>${esc(t)}</b><span class="small muted">${esc(d)}</span></a>`).join('')}</div>`;
  v.querySelectorAll('[data-go]').forEach(a=>a.onclick=e=>{ e.preventDefault(); state.tab=a.dataset.go; location.hash=a.dataset.go; render(); });
}

// ---------------------------------------------------------------- Data: the checklist above the existing page
async function dataChecks(el){
  let b; try { b = await brief(); } catch(e){ el.innerHTML = ''; return; }
  const items = b.health || [];
  el.innerHTML = `<div class="panel"><h2>Can the numbers be trusted?</h2><div class="note">Each check says what is wrong, what it hides, and who fixes it.</div>
    ${items.length ? items.map(h=>`<div class="check ${h.ok?'ok':'bad'}"><span class="mark">${h.ok?'✓':'!'}</span><div><b>${esc(h.title)}</b>${h.detail?`<div class="small">${esc(h.detail)}</div>`:''}${!h.ok&&h.who?`<div class="small muted">${esc(h.who)}</div>`:''}</div></div>`).join('') : '<div class="muted small">No checks yet.</div>'}</div>`;
}
