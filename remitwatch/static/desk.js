// RemitWatch category pages. Denials, Bundling & modifiers, Downcoding, Contracts & underpayment and the Worklist share one
// template: a title with one Start button, tiles that filter the list, what to do by kind of work, at most two fix-once
// notes, a 20-row list and one Details fold. Fees & takebacks has its own tiles, since nothing there has a filing window.
// Every number you can click opens a list whose count line shows that same number (/api/desk counts through the list's
// own filter). Loaded after today.js; uses its helpers and index.html's.

const DESKS = {
  denials:    {cat:'denial', title:'Denials', intro:'Lines and whole claims the payer paid $0 and has not paid since.'},
  bundling:   {cat:'bundling', title:'Bundling & modifiers', intro:'Visits, procedures and add-ons paid $0 because the payer folded them into another service.'},
  downcoding: {cat:'downcoding', title:'Downcoding', intro:'Visits paid at a lower level than billed, and procedure codes the payer changed.'},
  contracts:  {cat:'underpayment', title:'Contracts & underpayment', intro:"Claims paid below the payer's own usual rate, and contract rates that sit below Medicare."},
  worklist:   {cat: MONEY_CATS, title:'Worklist', intro:'Every item from the four claim pages in one list, soonest filing date first.'},
};
const BADGE_CAT = {denials:'denial', bundling:'bundling', downcoding:'downcoding', contracts:'underpayment'};
const DESK_TILES = [
  ['week',    'Closes in 7 days', {deadline:'week', status:'open,in_progress'}],
  ['valid',   'Window open',      {deadline:'valid', status:'open,in_progress'}],
  ['decide',  'To decide',        {deadline:'decide', status:'open,in_progress'}],
  ['sent',    'Sent, waiting',    {deadline:'', status:'appealed'}],
  ['expired', 'Window closed',    {deadline:'expired', status:'open,in_progress'}],
];
// the instruction for each kind of work (the brief carries the same words; these cover an older server)
const KIND_SHORT = {fix:'Correct it in EMA and resend it the way the item says.', auth:'Find the authorization and appeal, or ask for a retro-auth.',
  note:'Send the appeal letter with the visit note from EMA.', rule:'Send the appeal letter; it cites the coding rule.',
  rate:'Ask the payer to reprocess at its usual rate.', check:'Decide: other payer, the patient, appeal, or let go.'};
const ROWS = 20;
const DESK_DEFAULTS = {kind:'', payer:'', status:'open,in_progress', deadline:'valid', q:'', sort:'deadline', signal:'', paid:'', min:''};
const ALERT_FLOOR = 50000;          // a payer change worth less than $500 takes no line on a category page
const CHANGE_WORDS = {RAMP:'ramping up', RISING:'climbing', RESUMED:'back again'};
const BULK_WORDS = {in_progress:'Mark started', appealed:'Mark sent', dismissed:'Let go', open:'Back to To do'};

const short$ = c => { const d = Math.abs(c||0)/100; return (c<0?'-':'') + (d >= 100000 ? '$'+Math.round(d/1000)+'K' : '$'+Math.round(d).toLocaleString()); };
const num = n => Number(n||0).toLocaleString();
function shortOf(b, sig){ const l = labelOf(b, sig); return l.short || l.label; }
function kindLine(b, k){ return kindOf(b, k).short || KIND_SHORT[k] || ''; }
function foldIsOpen(key){ try { return localStorage.getItem('rw:fold:'+key) === '1'; } catch(e){ return false; } }
function foldRemember(key, open){ try { localStorage.setItem('rw:fold:'+key, open ? '1' : '0'); } catch(e){} }
function tileOf(f){ return DESK_TILES.find(([k, l, s]) => s.deadline === (f.deadline||'') && s.status === f.status); }
function viewName(f){
  const d = (WL_DEADLINES.find(x=>x[0]===(f.deadline||''))||['',''])[1], s = (WL_STATUSES.find(x=>x[0]===f.status)||['',f.status])[1];
  return [d, f.status === 'open,in_progress' ? '' : s].filter(Boolean).join(' · ');
}
// one line while the payment feed is behind: an item on the list may already be paid
function dataLine(b){
  const h = (b.health||[]).find(x=>!x.ok && /No payment newer/.test(x.title||''));
  return h ? `<div class="oneline"><span class="sev-medium">●</span> Payments after ${esc(longDate((b.newest_payment||'').slice(0,10)))} are not in yet; some items may already be paid. <a href="#data" class="go-data">Data</a></div>` : '';
}
async function payerList(){
  if(!state.payerList){ try { state.payerList = [...new Set((await api('/api/payers')).map(r=>r.payer_group).filter(Boolean))].sort(); } catch(e){ state.payerList = []; } }
  return state.payerList;
}
// a link to a page's Details (Bigger wins' "Rates by code and payer"): open the fold there and scroll to it once it has loaded
function goDetails(key){ foldRemember(key, true); state.foldScroll = key; state.tab = key; location.hash = key; render(); }
function goWins(anchor){ state.winsAnchor = anchor || null; state.tab = 'wins'; location.hash = 'wins'; render(); }
// the red number on a category tab: what closes within 7 days there, the page's first tile (from the brief)
function updateBadges(b){
  const bd = (b && b.badges) || {};
  Object.entries(BADGE_CAT).forEach(([k, cat]) => { const el = document.querySelector(`#nav [data-bdg="${k}"]`); if(!el) return;
    const n = (bd[cat]||{}).n || 0; el.textContent = n ? num(n) : ''; el.hidden = !n;
    el.parentElement.title = n ? `${pl(n, 'item')} ${n===1?'closes':'close'} within 7 days` : ''; });
}
// a click on the red number opens what it counts: the page's 7-day list, with no filter left over from before
function badgeOpen(key){ state.filt[key] = {deadline:'week', status:'open,in_progress', sort:'deadline'}; state.lim = state.lim || {}; state.lim[key] = ROWS; }

// ---------------------------------------------------------------- one category page
async function deskTab(v, key){
  const run = state.run;              // a newer render may start while this one waits; then this one writes nothing shared
  const D = DESKS[key], b = await brief();
  if(run !== state.run) return;
  if(!b.ready){ v.innerHTML = '<div class="panel"><h2>Nothing analysed yet</h2><div class="note">The first analysis run has not finished. Open Data and press Sync and analyse now.</div></div>'; return; }
  const f = Object.assign({}, DESK_DEFAULTS, state.filt[key]||{});
  state.lim = state.lim || {}; const lim = state.lim[key] || ROWS;
  const typed = String(f.q||'').slice(0,200);
  let sent = ''; try { sent = encodeURIComponent(typed); } catch(e){ sent = encodeURIComponent(typed.replace(/[\uD800-\uDFFF]/g,'')); }
  const find = Object.assign({cache:'no-store'}, sent ? {headers:{'X-Search':sent}} : {});
  const scope = {category:D.cat, live:1, payer:f.payer, status:f.status, deadline:f.deadline, signal:f.signal, paid:f.paid, min:f.min};
  const listUrl = n => '/api/findings?'+qs(Object.assign({}, scope, {kind:f.kind, sort:f.sort, limit:n, offset:0}));
  // the tiles count under the chosen kind of work and reason; the kinds of work under the chosen tile, payment window and search
  const [dk, res, why, payers] = await Promise.all([
    api('/api/desk?'+qs({category:D.cat, payer:f.payer, min:f.min, status:f.status, deadline:f.deadline, paid:f.paid, kind:f.kind, reason:f.signal}), find),
    api(listUrl(lim), find),
    f.kind ? api('/api/worklist/counts?'+qs(Object.assign({}, scope, {signal:'', kind:f.kind})), find) : Promise.resolve(null),
    payerList()]);
  if(run !== state.run) return;
  // the drawer walks this list, and fetches the next rows by itself at the end of it
  state.worklist = res.items.map(r=>r.finding_id); state.wlTotal = res.total; state.wlKey = key;
  state.wlNext = async () => {
    if(state.wlKey !== key) return 0;
    const seen = new Set(state.worklist||[]);
    const more = await api(listUrl((state.worklist||[]).length + ROWS), find);
    if(state.wlKey !== key) return 0;
    const fresh = more.items.map(r=>r.finding_id).filter(id=>!seen.has(id));
    state.worklist = (state.worklist||[]).concat(fresh); state.lim[key] = (state.lim[key]||ROWS) + ROWS;
    return fresh.length;
  };
  const T = dk.tiles, z = {n:0, cents:0}, t = k => T[k] || z;
  const tile = tileOf(f), onTile = k => tile && tile[0] === k && !f.paid;
  const closed = ['expired','missed'].includes(f.deadline), waiting = f.status === 'appealed';
  const tiles = DESK_TILES.filter(([k]) => ['week','valid','expired'].includes(k) || t(k).n || onTile(k)).map(([k, label]) => {
    const x = t(k), cls = k==='week' ? (x.n?'t-red':'t-ok') : k==='decide' ? 't-amber' : k==='expired' ? 't-grey' : '';
    const sub = k==='week' ? `${pl(t('month').n, 'item')} in 30 days`
      : k==='decide' ? `no filing date${t('older').n ? ` · <a href="#" class="see-older">+${num(t('older').n)} older</a>` : ''}`
      : k==='sent' ? (t('followup').n ? `<a href="#" class="see-followup due-now">${num(t('followup').n)} to follow up</a>` : 'waiting on the payer')
      : k==='expired' ? (t('missed').n ? `<a href="#" class="see-missed">${num(t('missed').n)} closed in the last 30 days</a>` : 'for the record')
      : 'you can still get back';
    return `<div class="tile ${cls} ${onTile(k)?'on':''}" role="button" tabindex="0" aria-pressed="${onTile(k)}" data-tile="${k}"><span class="t-l">${label}</span>
      <span class="t-v">${k==='week'&&!x.n ? 'None' : num(x.n)}<small>${x.n ? short$(x.cents) : ''}</small></span><span class="t-s">${sub}</span></div>`; }).join('');
  const parts = ['valid','decide','older','expired'], sumC = parts.reduce((a,k)=>a+t(k).cents,0), all = t('all');
  const allTip = `Window open ${num(t('valid').n)} + to decide ${num(t('decide').n)} + no filing date, older ${num(t('older').n)} + window closed ${num(t('expired').n)}`
    + (all.cents !== sumC ? `. ${money(all.cents - sumC)} of it is claims whose windows closed inside routine-rate patterns that still have claims open.` : '');
  const only = [f.kind ? kindOf(b, f.kind).label : '', f.signal ? shortOf(b, f.signal) : ''].filter(Boolean).join(' · ');
  // what to do, one row per kind of work under the chosen tile
  const kinds = dk.scope.kinds, single = kinds.length === 1;
  const reasons = (why && why.signals || []).filter(x=>x.signal && x.n);
  const showAll = state.allReasons === key + '|' + f.kind;
  const reasonChips = f.kind && reasons.length > 1 ? `<div class="why-chips">${(showAll ? reasons : reasons.slice(0,5)).map(x=>`<button class="sigchip ${f.signal===x.signal?'on':''}" data-sig="${esc(x.signal)}" title="${esc(labelOf(b, x.signal).label)}">${esc(shortOf(b, x.signal))} <span class="muted">${num(x.n)} · ${money(x.cents)}</span></button>`).join('')}${reasons.length>5 && !showAll ? `<button class="ghost small more-why">+${reasons.length-5} more</button>` : ''}</div>` : '';
  const doText = k => closed ? 'Window closed: nothing to send.' : waiting ? 'Sent: follow up if still unpaid 30 days after sending.' : kindLine(b, k);
  const pileRows = k => (k.piles||[]).map(p=>`<div class="pile-row" role="button" tabindex="0" data-kind="${k.key}" data-payer="${esc(p.payer)}"><span>${esc(p.payer)}</span><span class="tr-n"><b>${num(p.n)}</b> · ${money(p.cents)}</span><span></span></div>`).join('');
  const kindRows = kinds.map(k => { const top = (k.piles||[])[0];
    return `<div class="todo-row ${f.kind===k.key?'on':''}" data-kind="${k.key}" role="button" tabindex="0" aria-pressed="${f.kind===k.key}" aria-label="${esc(kindOf(b,k.key).label)}: ${esc(doText(k.key))} ${num(k.n)} items">${kindBadge(b, k.key)}<span class="tr-do ${closed||waiting?'muted':''}">${esc(doText(k.key))}</span>
      <span class="tr-n"><b>${num(k.n)}</b> · ${money(k.cents)}</span>
      <span class="tr-pile small muted">${!single && top && !f.payer ? `<a href="#" class="pile" data-kind="${k.key}" data-payer="${esc(top.payer)}">Most: ${esc(top.payer)} (${num(top.n)})</a>` : ''}</span></div>
      ${f.kind===k.key ? reasonChips : ''}
      ${single && !f.payer ? pileRows(k) : ''}`; }).join('')
    || '<div class="muted small" style="padding:6px 4px">Nothing here. Pick another box above.</div>';
  const fix = key === 'worklist' ? {} : fixOnce(key, b);
  const chip = (k, label) => `<span class="fchip">${label} <button data-clear="${k}" aria-label="Remove this filter">✕</button></span>`;
  const chips = [];
  if(f.kind) chips.push(chip('kind', esc(kindOf(b, f.kind).label)));
  if(f.signal) chips.push(chip('signal', esc(shortOf(b, f.signal))));
  if(f.payer) chips.push(chip('payer', esc(f.payer)));
  if(f.paid) chips.push(chip('paid', `Paid in the last ${esc(f.paid)} days`));
  if(f.q) chips.push(chip('q', `“${esc(f.q)}”`));
  if(!tile) chips.push(`<span class="fchip">${esc(viewName(f))} <button data-reset="1" aria-label="Back to Window open">✕</button></span>`);
  const sel = (name, label, opts, cur) => `<select data-f="${name}" aria-label="${label}">${opts.map(([val,l])=>`<option value="${esc(val)}" ${String(cur)===val?'selected':''}>${esc(l)}</option>`).join('')}</select>`;
  const plist = payers.concat(f.payer && !payers.includes(f.payer) ? [f.payer] : []);
  const orders = [['deadline','Soonest first'],['shortfall','Largest first'],['payer','By payer']].concat(waiting?[['sent','Oldest sent first']]:[]);
  const startLbl = closed || !res.items.length ? '' : f.sort === 'sent' ? 'Start with the oldest sent' : f.sort === 'shortfall' ? 'Start with the largest' : f.sort === 'deadline' ? 'Start with what closes first' : 'Start with the first item';
  const moreOpen = !!(state.moreF && state.moreF[key]);
  const oneKind = !!f.kind || single;
  const bulkSet = closed ? ['dismissed','open'] : waiting ? ['open','dismissed'] : ['in_progress','appealed','dismissed','open'];
  v.innerHTML = `<div class="page-head ph-row"><div><h1 class="page-title">${esc(D.title)}</h1> <span class="muted">${esc(D.intro)}</span></div>
      ${startLbl ? `<div class="ph-go"><button class="primary" id="startFirst">${startLbl}</button></div>` : ''}</div>
    ${dataLine(b)}
    <div class="tiles">${tiles}</div>
    ${key==='worklist' ? (only ? `<div class="small muted allopen">Counting ${esc(only)} only.</div>` : '') : `<div class="small muted allopen">${only ? `Counting ${esc(only)} only. ` : ''}<a href="#" class="see-all" title="${esc(allTip)}">Everything to do, any window: ${num(all.n)} · ${money(all.cents)}</a></div>`}
    <div class="todo-chips" aria-label="What to do">${kinds.map(k=>`<button class="seg ${f.kind===k.key?'on':''}" data-kind="${k.key}"><span>${esc(kindOf(b,k.key).label)} <span class="cnt">${num(k.n)}</span></span><span class="seg-sub">${money(k.cents)}</span></button>`).join('')}</div>
    ${f.kind || single ? `<div class="howline"><b>What to do:</b> ${esc(doText(f.kind || kinds[0].key))}</div>` : ''}
    <div class="ph-what">${f.kind ? reasonChips : ''}${single && !f.payer ? pileRows(kinds[0]) : ''}</div>
    <div class="desk-mid ${fix.side?'has-side':''}"><div class="todo-list"><div class="sec">What to do${tile && tile[0]!=='valid' ? ` <span class="muted small">(${esc(tile[1])})</span>` : !tile ? ` <span class="muted small">(${esc(viewName(f))})</span>` : ''}</div>${kindRows}</div>${fix.side||''}</div>
    <div class="fix-top">${fix.line||''}</div>
    <div class="list-head" id="listHead" tabindex="-1"><span class="lh-count"><b>${pl(res.total, 'item')}</b> · <b>${money(res.shortfall_cents)}</b></span>${chips.join('')}
      <span class="lh-tools">${sel('payer','Payer',[['','Any payer'],...plist.map(p=>[p,p])],f.payer)}${sel('min','Amount',[['','Any amount'],['20','Hide under $20'],['100','Hide under $100'],['500','Hide under $500']],f.min)}${sel('sort','Order',orders,f.sort)}
      <input data-f="q" maxlength="200" aria-label="Search" placeholder="${showPhi()?'Patient, claim ID or code':'Claim ID or code'}" value="${esc(f.q)}"><button class="ghost" id="moreF" aria-expanded="${moreOpen}">More filters</button><button class="ghost" id="wlCsv">Export</button></span></div>
    <div class="more-f" ${moreOpen?'':'hidden'}><span class="ph-only">${sel('min','Amount',[['','Any amount'],['20','Hide under $20'],['100','Hide under $100'],['500','Hide under $500']],f.min)}${sel('sort','Order',orders,f.sort)}<input data-f="q" maxlength="200" aria-label="Search" placeholder="Claim ID or code" value="${esc(f.q)}"></span>${sel('deadline','Filing date',WL_DEADLINES,f.deadline||'')}${sel('status','Status',WL_STATUSES,f.status)}<button class="ghost" id="wlClear">Reset all filters</button></div>
    <div class="bulk" id="bulk" hidden><span id="bulkN"></span>${bulkSet.map(s=>`<button data-bulk="${s}">${BULK_WORDS[s]}</button>`).join('')}</div>
    <div class="scroll"><table class="wl"><thead><tr><th><input type="checkbox" id="selAll" aria-label="Select every item shown"></th><th>Closes</th><th>Patient</th>${f.payer?'':'<th>Payer</th>'}<th>What happened</th><th class="num">Amount</th></tr></thead>
    <tbody>${res.items.map(r=>rowHtml(b, r, {oneKind, onePayer: !!f.payer, oneSignal: !!f.signal})).join('')}</tbody></table></div>
    ${res.items.length ? '' : `<div class="empty">Nothing here. ${chips.length ? 'Remove a filter above, or pick' : 'Pick'} another box at the top.</div>`}
    ${res.total > res.items.length ? `<button class="ghost" id="moreRows">Show ${Math.min(ROWS, res.total-res.items.length)} more</button>` : ''}
    <div class="fix-after">${fix.line||''}</div>
    ${key==='worklist' ? '' : `<details class="fold" id="fold" ${foldIsOpen(key)?'open':''}><summary>${esc(DETAILS_SUMMARY[key]||'Details')}</summary><div id="foldBody" class="muted small">Loading…</div></details>`}`;
  // a change redraws the page; the control that made it gets the focus back (or the list head, for a click that opens a list)
  const set = (patch, focus) => { state.filt[key] = Object.assign({}, f, patch); state.lim[key] = ROWS; state.refocus = focus || null; render(); };
  const keyed = (el, fn) => { el.onclick = fn; el.onkeydown = e => { if((e.key==='Enter'||e.key===' ') && e.target===el){ e.preventDefault(); fn(e); } }; };
  v.querySelectorAll('[data-tile]').forEach(el=>keyed(el, e=>{ if(e.target.closest && e.target.closest('a')) return;
    const k = el.dataset.tile, s = DESK_TILES.find(x=>x[0]===k)[2];
    set(Object.assign({paid:'', q:''}, s, {sort: k==='decide' ? 'shortfall' : (k==='sent' ? 'sent' : 'deadline')}), `[data-tile="${k}"]`); }));
  const link = (sel, patch, focus) => v.querySelectorAll(sel).forEach(a=>a.onclick=e=>{ e.preventDefault(); e.stopPropagation(); set(Object.assign({paid:'', q:''}, patch), focus); });
  link('.see-missed', {deadline:'missed', status:'open', sort:'deadline'}, '#listHead');
  link('.see-older', {deadline:'older', status:'open,in_progress', sort:'shortfall'}, '#listHead');
  link('.see-followup', {deadline:'followup', status:'appealed', sort:'sent'}, '#listHead');
  link('.see-all', {deadline:'', status:'open,in_progress', sort:'deadline'}, '#listHead');
  v.querySelectorAll('.todo-row').forEach(r=>keyed(r, e=>{ if(e.target.closest && e.target.closest('a.pile')) return;
    set({kind: f.kind===r.dataset.kind ? '' : r.dataset.kind, signal:''}, `.todo-row[data-kind="${r.dataset.kind}"]`); }));
  v.querySelectorAll('.todo-chips [data-kind]').forEach(bt=>bt.onclick=()=>set({kind: f.kind===bt.dataset.kind ? '' : bt.dataset.kind, signal:''}, `.todo-chips [data-kind="${bt.dataset.kind}"]`));
  v.querySelectorAll('[data-sig]').forEach(bt=>bt.onclick=()=>set({signal: f.signal===bt.dataset.sig ? '' : bt.dataset.sig}, `[data-sig="${bt.dataset.sig}"]`));
  v.querySelectorAll('.more-why').forEach(bt=>bt.onclick=()=>{ state.allReasons = key + '|' + f.kind; render(); });
  v.querySelectorAll('a.pile').forEach(a=>a.onclick=e=>{ e.preventDefault(); e.stopPropagation(); set({kind:a.dataset.kind, payer:a.dataset.payer, signal:''}, '#listHead'); });
  v.querySelectorAll('.pile-row').forEach(r=>keyed(r, ()=>set({kind:r.dataset.kind, payer:r.dataset.payer, signal:''}, '#listHead')));
  v.querySelectorAll('select[data-f]').forEach(el=>el.onchange=()=>{
    const patch = {[el.dataset.f]: el.value};
    if(el.dataset.f === 'deadline'){            // these two lists are about one status each
      if(el.value === 'followup') patch.status = 'appealed';
      else if(el.value === 'missed') patch.status = 'open';
      else if(['followup','missed'].includes(f.deadline)) patch.status = 'open,in_progress';
    }
    // 'Oldest sent first' belongs to the sent list: leaving it goes back to soonest first
    if((patch.sort || f.sort) === 'sent' && (patch.status || f.status) !== 'appealed') patch.sort = 'deadline';
    set(patch, `select[data-f="${el.dataset.f}"]`); });
  v.querySelectorAll('[data-clear]').forEach(bt=>bt.onclick=()=>set({[bt.dataset.clear]: ''}, '#listHead'));
  v.querySelectorAll('[data-reset]').forEach(bt=>bt.onclick=()=>set({deadline:'valid', status:'open,in_progress', sort:'deadline'}, '#listHead'));
  v.querySelectorAll('input[data-f="q"]').forEach(q=>q.onkeydown = e => { if(e.key==='Enter') set({q: q.value}, 'input[data-f="q"]'); });
  if(state.focusQ){ const q = v.querySelector('input[data-f="q"]'); if(q) q.focus(); state.focusQ = false; }
  v.querySelector('#moreF').onclick = () => { state.moreF = Object.assign({}, state.moreF, {[key]: !moreOpen}); const m = v.querySelector('.more-f'); m.hidden = moreOpen; v.querySelector('#moreF').setAttribute('aria-expanded', String(!moreOpen)); };
  v.querySelector('#wlClear').onclick = () => { state.filt[key] = {}; state.lim[key] = ROWS; render(); };
  v.querySelectorAll('.go-data').forEach(a=>a.onclick=e=>{ e.preventDefault(); state.tab='data'; location.hash='data'; render(); });
  v.querySelectorAll('.go-wins').forEach(a=>a.onclick=e=>{ e.preventDefault(); goWins(a.dataset.anchor); });
  v.querySelectorAll('.seeA').forEach(a=>a.onclick=e=>{ e.preventDefault(); const x = (state.alertsNew||[]).find(y=>y.alert_id===a.dataset.id); if(x && typeof seeAlert==='function') seeAlert(x); });
  const st = v.querySelector('#startFirst'); if(st) st.onclick = () => { if(res.items.length) openDetail(res.items[0].finding_id); };
  const mr = v.querySelector('#moreRows'); if(mr) mr.onclick = () => { const y = window.scrollY; state.lim[key] = lim + ROWS; render().then(()=>window.scrollTo(0, y)); };
  bindRows(v);
  bindBulk(v);
  v.querySelector('#wlCsv').onclick = () => exportCsv(Object.assign({}, scope, {kind:f.kind, view:'worklist'}), find, key);
  const fold = v.querySelector('#fold');
  if(fold){
    const load = () => { if(fold.open && !fold.dataset.done){ fold.dataset.done = '1';
      deskDetails(key, v.querySelector('#foldBody'), b, dk, set, f, find)
        .then(()=>{ if(state.foldScroll === key){ state.foldScroll = null; (v.querySelector('#dExtra .panel') || fold).scrollIntoView({block:'start'}); } })
        .catch(e=>{ const el = v.querySelector('#foldBody'); if(el) el.textContent = 'Could not load: ' + e.message; }); } };
    fold.ontoggle = () => { foldRemember(key, fold.open); load(); }; load();
  }
  if(state.refocus){ const el = v.querySelector(state.refocus); state.refocus = null; if(el){ el.focus({preventScroll: el.id !== 'listHead'}); if(el.id === 'listHead') el.scrollIntoView({block:'start'}); } }
  if(f.first === '1' && res.items.length){ state.filt[key] = Object.assign({}, f, {first:''}); openDetail(res.items[0].finding_id); }
}
const DETAILS_SUMMARY = {denials:'Details: by reason, by month, what each label means', bundling:'Details: by reason, by month, the 2027 changes, label meanings',
  downcoding:'Details: by reason, plans, what gets cut, by clinician', contracts:'Details: rates against Medicare by code and payer, by reason'};

function bindRows(v){
  v.querySelectorAll('tr.row[data-id]').forEach(tr=>{ tr.onclick = e => { if(e.target.closest('input')) return; openDetail(tr.dataset.id); };
    tr.onkeydown = e => { if((e.key==='Enter'||e.key===' ') && e.target===tr){ e.preventDefault(); openDetail(tr.dataset.id); } }; });
}
function bindBulk(v){
  const bulk = v.querySelector('#bulk'), bulkN = v.querySelector('#bulkN'); if(!bulk) return;
  const picked = () => [...v.querySelectorAll('.sel:checked')].map(x=>x.value);
  const sync = () => { const n = picked().length; bulk.hidden = !n; bulkN.textContent = n + ' selected'; };
  v.querySelectorAll('.sel').forEach(cb=>cb.onchange=sync);
  const all = v.querySelector('#selAll'); if(all) all.onchange = e => { v.querySelectorAll('.sel').forEach(cb=>cb.checked = e.target.checked); sync(); };
  v.querySelectorAll('[data-bulk]').forEach(bt=>bt.onclick=async()=>{ const ids = picked(); if(!ids.length) return;
    if(bt.dataset.bulk==='dismissed' && !confirm(`Let go of ${pl(ids.length, 'item')}? They leave the list and count as let go.`)) return;
    if(bt.dataset.bulk==='appealed' && ids.length > 5 && !confirm(`Mark ${pl(ids.length, 'item')} as sent to the payer? They leave the to-do list until a follow-up is due.`)) return;
    bt.disabled = true;
    try { await post('/api/findings/bulk-status', {ids, status: bt.dataset.bulk}); } catch(e){ alert('Not saved: '+e.message); bt.disabled = false; return; }
    state.brief = null; render(); });
}
async function exportCsv(params, find, key){      // fetched, not linked: the search text travels in a header, never in the address
  let r;
  try { r = await fetch('/api/findings.csv?'+qs(params), find); } catch(e){ alert('The export did not come back (no connection).'); return; }
  if(!r.ok){ alert('The export did not come back ('+r.status+').'); return; }
  const url = URL.createObjectURL(await r.blob()), a = document.createElement('a'); a.href = url; a.download = `remitwatch-${key}.csv`; document.body.appendChild(a); a.click(); a.remove(); setTimeout(()=>URL.revokeObjectURL(url), 10000);
}
// a tick box and five columns: no status column (a pill under the amount when the status is not To do), the date in a tooltip.
// An item already sent, paid or let go shows that in grey instead of a filing date in red.
function rowHtml(b, r, o){
  o = o || {};
  const due = r.next_deadline || r.appeal_deadline, c = closesText(due), lb = labelOf(b, r.signal), est = /not confirmed/.test(r.deadline_basis||'');
  const live = !r.status || r.status === 'open' || r.status === 'in_progress';
  const when = live ? `<span class="${c.cls}" title="${esc(due ? 'File by '+due+(est?' (estimated rule: confirm it in the payer agreement)':'')+(r.next_deadline&&r.next_deadline!==r.appeal_deadline?'; the first of its claims':'') : 'No filing date')}">${esc(c.text)}${est&&due?'<sup class="muted">*</sup>':''}</span>`
    : `<span class="muted">${esc(r.status === 'appealed' && r.appealed_at ? 'sent ' + longDate(String(r.appealed_at).slice(0,10)) : String(STATUS_WORDS[r.status]||r.status).toLowerCase())}</span>`;
  const amt = r.window_cents != null ? `<b>${money2(r.window_cents)}</b><div class="small muted">${pl(r.window_lines, 'line')} in range</div>`
    : (r.shortfall_cents ? `<b>${money2(r.shortfall_cents)}</b>` : '<span class="small muted" title="No usual rate to compare with">not known</span>');
  const pill = r.status && r.status !== 'open' ? `<div><span class="st st-${esc(r.status)}">${esc(STATUS_WORDS[r.status]||r.status)}</span></div>` : '';
  const code = `${esc(r.original_procedure_code?r.original_procedure_code+'→':'')}${esc(r.procedure_code||'')}${r.modifiers?'-'+esc(r.modifiers):''}${r.carc?' · '+esc(r.carc):''}`;
  return `<tr class="row" tabindex="0" data-id="${esc(r.finding_id)}"><td><input type="checkbox" class="sel" value="${esc(r.finding_id)}" aria-label="Select this item"></td>
    <td class="day">${when}</td>
    <td class="who">${esc(r.patient||'')}<div class="small muted">${r.patient_control_number?'claim '+esc(r.patient_control_number):''}</div></td>
    ${o.onePayer ? '' : `<td class="payer">${esc(r.payer_group)}</td>`}
    <td class="what" title="${esc(lb.label)}">${o.oneSignal ? '' : `${o.oneKind ? '' : kindBadge(b, kindFor(b, r))+' '}${esc(shortOf(b, r.signal))}`}<div class="small muted">${code}</div>${r.hint?`<div class="small"><span class="chip hint">${esc(r.hint)}</span></div>`:''}</td>
    <td class="num">${amt}${pill}</td></tr>`;
}

// At most two lines of money fixed once or payer behaviour. Never a claim, never a status button.
function fixOnce(key, b){
  const causes = b.causes||[];
  const catOf = {denials:/^(DENIED_|CLAIM_DENIED)/, bundling:/^(EM_BUNDLED|ADDON_BUNDLED|PROCEDURE_BUNDLED)/};
  const fam = {denials:['denial'], bundling:['bundling'], downcoding:['downcoding','code_change'], contracts:['underpayment','rate']}[key]||[];
  const trend = x => !x.n_prev ? 'new' : (x.cents90 > x.cents_prev*1.15 ? 'rising' : (x.cents90 < x.cents_prev*0.85 ? 'falling' : 'steady'));
  const line = (tag, txt, link) => `<div class="fixonce"><span class="fo-tag">${tag}</span> ${txt} ${link||''}</div>`;
  const dated = [], rising = [], change = [], steady = [];
  if(key==='bundling' && localDay() < '2027-01-01') dated.push(line('Before Jan 1', 'G2211 becomes modifier B1: change the EMA charge setup. Billing.', '<a href="#wins" class="go-wins" data-anchor="wins-2027">Bigger wins</a>'));
  if(catOf[key]){
    const top = causes.filter(x=>catOf[key].test(x.signal)).sort((a,c)=>c.cents90-a.cents90)[0];
    if(top) (trend(top)==='rising' ? rising : steady).push(line('Fix once', `<b>${esc(top.label)}:</b> ${money(top.cents90)} in 90 days, ${trend(top)}. ${esc(OWNER_OF[top.work]||'Billing')}.`, '<a href="#wins" class="go-wins" data-anchor="wins-leaks">Bigger wins</a>'));
  }
  // a payer behaving differently, in a few words; the alert has the detail
  const a = (state.alertsNew||[]).filter(x=>fam.includes(x.family) && x.kind!=='PROVIDER_TARGETED' && (x.cents_recent||0) >= ALERT_FLOOR)[0];
  if(a){
    const what = typeof FAMILY_LABELS !== 'undefined' && FAMILY_LABELS[a.family] && CHANGE_WORDS[a.kind]
      ? `${esc(a.payer_group||'A payer')}: ${esc(FAMILY_LABELS[a.family].toLowerCase())} ${CHANGE_WORDS[a.kind]}, worth ${money(a.cents_recent)}.`
      : esc(String(a.title||'').replace(/\.$/,'')) + '.';
    change.push(line('Payer change', what, `<a href="#" class="seeA" data-id="${esc(a.alert_id)}">See them</a>`));
  }
  if(key==='contracts'){
    const c = ((b.contracts||{}).payers||[]).filter(p=>p.gap_cents>0).slice(0,4), cap = ((b.fees||{}).charge_caps||[])[0];
    if(!c.length && !cap) return {line: change.join('')};
    return {line: change.join(''), side: `<div class="fo-side"><div class="sec"><span class="fo-tag">Fix once</span> Rates below Medicare, ${esc(b.contracts.year||'')}</div>
      ${c.length ? `<table class="mini"><tbody>${c.map(p=>`<tr><td>${esc(p.payer_group)}</td><td class="num ${p.pct_of_medicare<0.9?'sev-high':'sev-medium'}">${Math.round(p.pct_of_medicare*100)}%</td><td class="num"><b>${money(p.gap_cents)}</b>/yr</td></tr>`).join('')}</tbody></table>` : ''}
      ${cap && cap.top_rate_cents ? `<div class="small">Your fee for <b>${esc(cap.code)}</b> is ${money2(cap.charge_cents)}; a payer allows ${money2(cap.top_rate_cents)}. Raise it.</div>` : ''}
      <div class="small muted">Owner: bring the signed fee schedules to the contract talk. <a href="#wins" class="go-wins" data-anchor="wins-rates">Bigger wins</a></div></div>`};
  }
  return {line: dated.concat(rising, change, steady).slice(0,2).join('')};
}

// ---------------------------------------------------------------- the Details fold
async function deskDetails(key, el, b, dk, set, f, find){
  const D = DESKS[key];
  // counted under the page's own payer, amount, payment window and search, so each number opens a list of that size
  const base = {category:D.cat, live:1, status:'open,in_progress', payer:f.payer, min:f.min, paid:f.paid};
  const [all, inw] = await Promise.all([api('/api/worklist/counts?'+qs(base), find), api('/api/worklist/counts?'+qs(Object.assign({}, base, {deadline:'valid'})), find)]);
  const inMap = Object.fromEntries((inw.signals||[]).map(s=>[s.signal, s]));
  const sigs = (all.signals||[]).filter(s=>s.signal);
  const T = dk.tiles||{}, t = k => T[k]||{n:0,cents:0}, parts = ['valid','decide','older','expired'];
  const sumN = parts.reduce((a,k)=>a+t(k).n,0), sumC = parts.reduce((a,k)=>a+t(k).cents,0), allT = t('all');
  const recon = f.kind || f.signal ? '' : `The tiles: window open ${num(t('valid').n)}${t('decide').n?`, to decide ${num(t('decide').n)}`:''}${t('older').n?`, no filing date and older ${num(t('older').n)}`:''}, window closed ${num(t('expired').n)}: ${num(sumN)} items · ${money(sumC)}.`
    + (allT.cents !== sumC ? ` Everything to do comes to ${money(allT.cents)}; the other ${money(allT.cents - sumC)} is claims whose windows closed inside routine-rate patterns that still have claims open (those patterns are under Window open).` : '');
  const cell = (s, dl) => s ? `<a href="#" class="rlink" data-rsig="${esc(s.signal)}" data-dl="${dl}">${num(s.n)} · ${money(s.cents)}</a>` : '–';
  el.classList.remove('muted','small');
  el.innerHTML = `${recon ? `<div class="small muted" style="margin:0 0 8px">${esc(recon)}</div>` : ''}
    <div id="dExtra"></div>
    <div class="grid two"><div class="panel"><h2>By reason</h2><table><thead><tr><th>What happened</th><th class="num">Window open</th><th class="num">All to do</th></tr></thead><tbody>${sigs.map(s=>`<tr><td>${esc(shortOf(b,s.signal))}<div class="small muted"><code>${esc(s.signal)}</code></div></td><td class="num">${cell(inMap[s.signal], 'valid')}</td><td class="num">${cell(s, '')}</td></tr>`).join('')}</tbody></table>
      <div class="small muted">Click a number to list those items.</div></div>
    <div class="panel"><h2>What each label means</h2>${sigs.map(s=>`<div class="small" style="margin:4px 0"><b>${esc(labelOf(b,s.signal).label)}</b> <code class="muted">${esc(s.signal)}</code><div class="muted">${esc(SIGNAL_HELP[s.signal]||'')}</div></div>`).join('')}</div></div>
    <div class="panel"><canvas id="cTrendD"></canvas></div>`;
  el.querySelectorAll('.rlink').forEach(a=>a.onclick=e=>{ e.preventDefault(); set({signal: a.dataset.rsig, kind:'', deadline: a.dataset.dl, status:'open,in_progress', sort:'deadline'}, '#listHead'); });
  await trendChart('cTrendD', {category:D.cat}, 'Lines per month by payer (every status)');
  const ex = el.querySelector('#dExtra');
  if(key === 'downcoding'){
    ex.innerHTML = '<div class="muted small">Loading…</div>';
    // its own year and status: the pickers that Overview uses are hidden here
    try { await downcodeWho(ex, {year:'', status:'open'}); }
    catch(e){ ex.innerHTML = `<div class="panel"><div class="note">What each clinician is doing is kept for the owner on this site: open Providers and enter the owner's password, then come back.</div></div>`; }
  } else if(key === 'contracts'){
    await contractsDetails(ex, b);
  } else if(key === 'bundling'){
    ex.innerHTML = `<div class="panel"><h2>What changes on January 1, 2027</h2><ul class="wins-list"><li>When a modifier-25 visit and a 0-day procedure fall on the same day, Medicare pays the most expensive service in full and every other service that day at 80%.</li><li>G2211 becomes modifier B1 on the visit itself: change the charge setup in EMA before January.</li></ul><a href="#wins" class="go-wins small" data-anchor="wins-2027">More on Bigger wins</a></div>`;
    ex.querySelectorAll('.go-wins').forEach(a=>a.onclick=e=>{ e.preventDefault(); goWins(a.dataset.anchor); });
  }
}
async function contractsDetails(el, b){
  const year = (b.contracts||{}).year || '';
  const tier = state.filt.tier || 'physician';
  const mx = await api('/api/rates/matrix?'+qs({year, tier}));
  const ps = (b.contracts||{}).payers||[];
  el.innerHTML = `<div class="panel"><h2>Allowed as a share of Medicare, ${esc(year)} <select id="tierSel" aria-label="Clinician type"><option value="physician" ${tier==='physician'?'selected':''}>physicians</option><option value="nonphysician" ${tier==='nonphysician'?'selected':''}>PAs and NPs</option></select></h2>
      <div class="note">Each payer's usual allowed amount for a code, against what Medicare paid this practice for the same code and clinician type that year. Red is under 80%, amber under 95%.</div>
      <div class="scroll"><table class="heat"><thead><tr><th>Payer</th>${mx.codes.map(c=>`<th class="num">${esc(c)}</th>`).join('')}</tr></thead>
      <tbody>${mx.payers.slice(0,25).map(p=>`<tr><td style="text-align:left">${esc(p.payer_group)}</td>${mx.codes.map(c=>{ const x = p.codes[c]; if(!x) return '<td class="muted">·</td>';
        const col = x.pct==null ? '' : (x.pct<0.8 ? 'var(--bad)' : (x.pct<0.95 ? 'var(--warn)' : 'var(--good)'));
        return `<td class="num" title="${money2(x.cents)}" style="color:${col}">${x.pct==null?money2(x.cents):Math.round(x.pct*100)+'%'}</td>`; }).join('')}</tr>`).join('')}</tbody></table></div></div>
    ${ps.length ? `<div class="panel"><h2>Every payer against Medicare, ${esc(year)}</h2><table><thead><tr><th>Payer</th><th class="num">All office services</th><th class="num">Office visits</th><th class="num">A year below Medicare</th></tr></thead>
      <tbody>${ps.map(p=>`<tr><td>${esc(p.payer_group)}</td><td class="num">${Math.round(p.pct_of_medicare*100)}%</td><td class="num">${p.visit_pct_of_medicare!=null?Math.round(p.visit_pct_of_medicare*100)+'%':'–'}</td><td class="num">${p.gap_cents>0?money(p.gap_cents):'–'}</td></tr>`).join('')}</tbody></table></div>` : ''}`;
  el.querySelector('#tierSel').onchange = e => { state.filt.tier = e.target.value; contractsDetails(el, b); };
}

// ---------------------------------------------------------------- Fees & takebacks
// Its tiles switch the list: payment fees by payer (a request to each payer, fixed once), the takebacks of the last 90
// days (to check against the payer's letter) and every takeback (the record).
async function feesDesk(v){
  const run = state.run;
  const b = await brief();
  if(run !== state.run) return;
  if(!b.ready){ v.innerHTML = '<div class="panel"><h2>Nothing analysed yet</h2></div>'; return; }
  const f = Object.assign({list:'takebacks', paid:'90'}, state.filt.feesDesk||{});
  state.lim = state.lim || {}; const lim = state.lim.feesDesk || ROWS;
  const tb = {signal:'TAKEBACK', status:'open,in_progress', live:1};
  const tbUrl = n => '/api/findings?'+qs(Object.assign({}, tb, {paid:f.paid, sort:'paid', limit:n}));
  const [tb90, tbAll, tbBig, dk, tbl, fp] = await Promise.all([
    api('/api/findings?'+qs(Object.assign({}, tb, {paid:90, limit:1}))), api('/api/findings?'+qs(Object.assign({}, tb, {limit:1}))),
    api('/api/findings?'+qs(Object.assign({}, tb, {min:5000, limit:1}))),
    api('/api/desk?'+qs({category:'fees', signal:'TAKEBACK', status:'open,in_progress', paid:90, tiles:0})),
    api(tbUrl(lim)), api('/api/fees/payers')]);
  if(run !== state.run) return;
  const onFees = f.list === 'fees', on90 = !onFees && f.paid === '90', onAll = !onFees && !f.paid;
  if(onFees){ state.worklist = null; state.wlNext = null; state.wlTotal = null; state.wlKey = null; }
  else {
    state.worklist = tbl.items.map(r=>r.finding_id); state.wlTotal = tbl.total; state.wlKey = 'fees';
    state.wlNext = async () => {
      if(state.wlKey !== 'fees') return 0;
      const seen = new Set(state.worklist||[]), more = await api(tbUrl((state.worklist||[]).length + ROWS));
      if(state.wlKey !== 'fees') return 0;
      const fresh = more.items.map(r=>r.finding_id).filter(id=>!seen.has(id));
      state.worklist = (state.worklist||[]).concat(fresh); state.lim.feesDesk = (state.lim.feesDesk||ROWS) + ROWS;
      return fresh.length;
    };
  }
  const top = ((dk.scope.kinds[0]||{}).piles||[])[0], since = ((state.meta||{}).remits||{}).first_payment;
  const older = fp.payers.length - fp.payers12;
  const startLbl = onFees ? (fp.payers.length ? 'Start with the biggest payer' : '') : (on90 && tbl.items.length ? 'Start with the newest takeback' : '');
  v.innerHTML = `<div class="page-head ph-row"><div><h1 class="page-title">Fees & takebacks</h1> <span class="muted">Money taken out of payments: vendor fees to stop once, and takebacks to check against the payer's letter.</span></div>
      ${startLbl ? `<div class="ph-go"><button class="primary" id="startFirst">${startLbl}</button></div>` : ''}</div>
    ${dataLine(b)}
    <div class="tiles">
      <div class="tile ${onFees?'on':''}" role="button" tabindex="0" data-l="fees"><span class="t-l">Payment fees, last 12 months</span><span class="t-v">${money(fp.cents12)}</span>
        <span class="t-s">${fp.from ? `${esc(rwMonth(fp.from))} to ${esc(rwMonth(fp.to))}, ${pl(fp.payers12, 'payer')}` : ''}${fp.since ? `; ${money(fp.cents)} since ${esc(rwMonth(fp.since))}` : ''}</span></div>
      <div class="tile ${tb90.total?'t-amber':'t-ok'} ${on90?'on':''}" role="button" tabindex="0" data-l="takebacks" data-paid="90"><span class="t-l">Taken back, last 90 days</span><span class="t-v">${tb90.total ? num(tb90.total) : 'None'}<small>${tb90.total ? short$(tb90.shortfall_cents) : ''}</small></span><span class="t-s">check each one</span></div>
      <div class="tile t-grey ${onAll?'on':''}" role="button" tabindex="0" data-l="takebacks" data-paid=""><span class="t-l">All takebacks</span><span class="t-v">${num(tbAll.total)}<small>${tbAll.total ? short$(tbAll.shortfall_cents) : ''}</small></span><span class="t-s">${since ? 'since '+esc(rwMonth(since.slice(0,7)))+' · ' : ''}${num(tbBig.total)} of $5,000 or more</span></div></div>
    <div class="todo-list"><div class="sec">What to do</div>
      <div class="todo-row ${onFees?'on':''}" role="button" tabindex="0" data-l="fees"><span class="fo-tag">Fix once</span><span class="tr-do">Ask each payer or its vendor for direct deposit with no fee.</span><span class="tr-n"><b>${pl(fp.payers12, 'payer')}</b> · ${money(fp.cents12)} in 12 months</span><span class="tr-pile small muted">${fp.payers[0] ? 'Most: '+esc(fp.payers[0].payer_name) : ''}</span></div>
      <div class="todo-row ${on90?'on':''}" role="button" tabindex="0" data-l="takebacks" data-paid="90"><span class="kind k-check">Check first</span><span class="tr-do">${tb90.total ? "Match each takeback to the payer's letter; dispute any not owed." : 'No takebacks in the last 90 days.'}</span><span class="tr-n"><b>${num(tb90.total)}</b> · ${money(tb90.shortfall_cents||0)}</span><span class="tr-pile small muted">${top ? `Most: ${esc(top.payer)} (${num(top.n)})` : ''}</span></div></div>
    ${onFees ? feeListHtml(fp, older) : `<div class="list-head" id="listHead" tabindex="-1"><span class="lh-count"><b>${pl(tbl.total, 'takeback')}</b> · <b>${money(tbl.shortfall_cents)}</b>${on90 ? ' in the last 90 days' : ', every one in the files'}</span></div>
        <div class="bulk" id="bulk" hidden><span id="bulkN"></span>${['in_progress','appealed','dismissed','open'].map(s=>`<button data-bulk="${s}">${BULK_WORDS[s]}</button>`).join('')}</div>
        <div class="scroll"><table class="wl"><thead><tr><th><input type="checkbox" id="selAll" aria-label="Select every takeback shown"></th><th>Taken</th><th>Payer</th><th class="num">Taken back</th></tr></thead>
        <tbody>${tbl.items.map(r=>`<tr class="row" tabindex="0" data-id="${esc(r.finding_id)}"><td><input type="checkbox" class="sel" value="${esc(r.finding_id)}" aria-label="Select this takeback"></td><td class="day">${esc(rwMonth((r.payment_date||'').slice(0,7)))}</td><td class="payer">${esc(r.payer_group)}</td><td class="num"><b>${money2(r.shortfall_cents)}</b>${r.status!=='open'?`<div><span class="st st-${esc(r.status)}">${esc(STATUS_WORDS[r.status]||r.status)}</span></div>`:''}</td></tr>`).join('')}</tbody></table></div>
        ${tbl.items.length ? '' : `<div class="empty">${on90 ? 'No takebacks in the last 90 days. All takebacks are in the grey box above.' : 'No takebacks.'}</div>`}
        ${tbl.total > tbl.items.length ? `<button class="ghost" id="moreRows">Show ${Math.min(ROWS, tbl.total-tbl.items.length)} more</button>` : ''}`}
    <details class="fold" id="fold" ${foldIsOpen('fees')?'open':''}><summary>Details: money held back by month, MIPS and the 2% reduction</summary><div id="foldBody" class="muted small">Loading…</div></details>`;
  const go = (l, paid) => { state.filt.feesDesk = {list: l, paid: paid || ''}; state.lim.feesDesk = ROWS; render(); };
  v.querySelectorAll('[data-l]').forEach(el=>{ const fn = () => go(el.dataset.l, el.dataset.paid != null ? el.dataset.paid : f.paid);
    el.onclick = fn; el.onkeydown = e => { if((e.key==='Enter'||e.key===' ') && e.target===el){ e.preventDefault(); fn(); } }; });
  v.querySelectorAll('.go-data').forEach(a=>a.onclick=e=>{ e.preventDefault(); state.tab='data'; location.hash='data'; render(); });
  v.querySelectorAll('tr.fee-row, button.fee-letter').forEach(el=>{ const fn = e => { if(e) e.stopPropagation(); openFeeLetter(fp.payers[Number(el.dataset.i)]); };
    el.onclick = fn; if(el.tagName === 'TR') el.onkeydown = e => { if((e.key==='Enter'||e.key===' ') && e.target===el){ e.preventDefault(); fn(); } }; });
  const st = v.querySelector('#startFirst'); if(st) st.onclick = () => { if(onFees){ if(fp.payers[0]) openFeeLetter(fp.payers[0]); } else if(tbl.items.length) openDetail(tbl.items[0].finding_id); };
  const mr = v.querySelector('#moreRows'); if(mr) mr.onclick = () => { const y = window.scrollY; state.lim.feesDesk = lim + ROWS; render().then(()=>window.scrollTo(0, y)); };
  bindRows(v);
  bindBulk(v);
  const fold = v.querySelector('#fold');
  const load = () => { if(fold.open && !fold.dataset.done){ fold.dataset.done = '1'; feesDetails(v.querySelector('#foldBody')).catch(e=>{ const el = v.querySelector('#foldBody'); if(el) el.textContent = 'Could not load: '+e.message; }); } };
  fold.ontoggle = () => { foldRemember('fees', fold.open); load(); }; load();
}
// payment fees by payer as the remittance names it: the one to write to
function feeListHtml(fp, older){
  const asked = fp.asked || [];
  return `<div class="list-head" id="listHead" tabindex="-1"><span class="lh-count"><b>${pl(fp.payers12, 'payer')}</b> still charging · <b>${money(fp.cents12)}</b> in the last 12 full months${older ? `; ${num(older)} more with no fee in that time` : ''}</span></div>
    <div class="small muted" style="margin:0 2px 6px">Open a payer for its request letter, send it, then press Mark asked.</div>
    <div class="scroll"><table class="fees"><thead><tr><th>Payer</th><th class="num">Last 12 months</th><th class="num">Since ${esc(rwMonth(fp.since))}</th><th class="num">Months with a fee</th><th>Last fee</th><th></th></tr></thead>
    <tbody>${fp.payers.map((p,i)=>`<tr class="row fee-row" tabindex="0" data-i="${i}"><td>${esc(p.payer_name)}${p.payer_group && p.payer_group !== p.payer_name ? `<div class="small muted">${esc(p.payer_group)}</div>` : ''}</td><td class="num">${p.cents12 ? money(p.cents12) : '–'}</td><td class="num">${money(p.cents)}</td><td class="num">${num(p.months)}</td><td>${esc(rwMonth(p.last_month))}</td><td><button class="ghost small fee-letter" data-i="${i}">Request letter</button></td></tr>`).join('')}</tbody></table></div>
    ${fp.payers.length ? '' : '<div class="empty">No payment fees to stop.</div>'}
    ${asked.length ? `<div class="small muted" style="margin-top:10px"><b>Asked, waiting for the change:</b> ${asked.map(a=>`${esc(a.payer_name)}${a.asked ? ` (asked ${esc(longDate(String(a.asked).slice(0,10)))})` : ''}`).join(' · ')}</div>` : ''}`;
}
// the request letter for one payer, and one filled button: Mark asked moves its fee months to Sent
async function openFeeLetter(p){
  if(!p) return;
  let L; try { L = await api('/api/fees/letter?'+qs({payer: p.payer_name})); } catch(e){ alert('The letter did not come back: '+e.message); return; }
  $('#modalBox').innerHTML = `<div style="display:flex;justify-content:space-between;gap:8px"><h2 style="margin:0">Payment fees: ${esc(p.payer_name)}</h2><button id="closeM" aria-label="Close">✕</button></div>
    <div class="note">${esc(L.evidence)} Send it through the payer's provider portal, by fax or by mail, then press Mark asked: its ${pl(p.ids.length, 'fee month')} move to Sent. Fees that keep arriving afterwards show here again.</div>
    <div class="actions"><button class="primary" id="feeAsked">Mark asked</button><button id="copyL">Copy letter</button></div>
    <div class="st-err sev-high small" role="alert"></div>
    <pre id="letter">${esc(L.letter)}</pre>`;
  $('#modal').classList.add('open');
  $('#closeM').onclick = () => $('#modal').classList.remove('open');
  $('#copyL').onclick = () => navigator.clipboard.writeText(L.letter);
  $('#feeAsked').onclick = async () => { const bt = $('#feeAsked'); bt.disabled = true;
    try { await post('/api/findings/bulk-status', {ids: p.ids, status: 'appealed'}); }
    catch(e){ bt.disabled = false; const er = $('#modalBox .st-err'); if(er) er.textContent = 'Not saved: ' + e.message; return; }
    $('#modal').classList.remove('open'); state.brief = null; render(); };
  $('#feeAsked').focus();
}
const PLB_WORDS = {WO:'Taken back', FB:'Forwarding balance', CS:'Adjustment (payment fees)', AH:'Origination fee', L6:'Interest', 72:'Authorized return', J1:'Non-reimbursable'};
async function feesDetails(el){
  const [vol] = await Promise.all([api('/api/volume'), state.summary ? Promise.resolve() : loadSummary()]);
  const months = [...new Set(vol.plb.map(r=>r.month))].sort(), codes = Object.keys(PLB_WORDS);
  const info = sig => ((state.summary||{}).info||[]).filter(r=>r.signal===sig).sort((a,c)=>String(a.year).localeCompare(String(c.year)));
  const mips = info('MIPS_ADJUSTMENT'), seq = info('SEQUESTRATION');
  el.classList.remove('muted','small');
  el.innerHTML = `<div class="panel"><h2>Money held back by month</h2><canvas id="cPlb"></canvas></div>
    <div class="grid two">
      <div class="panel"><h2>Medicare quality program (MIPS) <span class="chip">For information</span></h2>${mips.length ? `<table><tbody>${mips.map(r=>`<tr><td>${esc(r.year)}</td><td class="num">${money(r.shortfall_cents)}</td></tr>`).join('')}</tbody></table><div class="small muted">Net adjustment on Medicare lines; a positive number is a bonus.</div>` : '<div class="muted small">None found.</div>'}</div>
      <div class="panel"><h2>Federal 2% reduction <span class="chip">For information</span></h2>${seq.length ? `<table><tbody>${seq.map(r=>`<tr><td>${esc(r.year)}</td><td class="num">${money(r.shortfall_cents)}</td></tr>`).join('')}</tbody></table><div class="small muted">Withheld on Medicare and Medicare Advantage lines by law; not recoverable.</div>` : '<div class="muted small">None found.</div>'}</div></div>`;
  chart('cPlb', {type:'bar', data:{labels:months, datasets:codes.map((c,i)=>({label:PLB_WORDS[c], backgroundColor:palette[i], data:months.map(m=>{ const r = vol.plb.find(x=>x.month===m && x.reason_code===c); return r ? r.cents/100 : 0; })}))},
    options:{scales:{x:{stacked:true}, y:{stacked:true, ticks:{callback:x=>'$'+x.toLocaleString()}}}, plugins:{legend:{position:'bottom'}}}});
}
