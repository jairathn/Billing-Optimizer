// RemitWatch write-off policy page (#policy): the owner's decisions on what EMA writes off, one row per key:
//  - write-off patterns (payer, code, modifier and reason of denials written off in EMA, by its posting rules or later by
//    a person): Pursue (the default: they stay in the work queue) or Accept (the next run takes them off the queue as
//    written off by the policy; they stay findable, and Pursue brings them back);
//  - codes a payer almost never pays: the run keeps their denials out of Work now until the owner decides (nothing is
//    accepted by default): Pursue lists every one in the Worklist at the next run, Accept leaves them written off;
//  - denial reasons EMA posts as contractual (the payer's reason on every code): Accept as policy, or Pursue (have EMA
//    post the reason as non-contractual so its lines reach the denial queue).
// Any signed-in person may decide, as with the hide rules; the name given and the date are kept. A decision changes its
// row in place, so the filter, "Show all" and the place on the page stay. Loaded after today.js; uses index.html's helpers
// ($, api, esc, money, money2, qs, state, render, openDetail) and today.js's (longDate, pl, closesText). Names here start
// with pol (one global scope with the other scripts).

const POL_NAME_KEY = 'rw:policy:name';
const POL_PAGE = 50;
const POL_SHOW = 40;        // patterns shown before "Show all" (what waits for a decision, then most dollars still appealable); the filter searches all
const POL_KIND = {write_off: 'Written off in EMA', payer_policy: 'A code the payer almost never pays', contractual: 'Posted by EMA as contractual'};

function polName(){ try { return localStorage.getItem(POL_NAME_KEY) || ''; } catch(e){ return ''; } }
// a phone (390 px): one card per pattern, so the decision buttons stay on screen
function polNarrow(){ return typeof window !== 'undefined' && window.innerWidth > 0 && window.innerWidth <= 640; }
function polRemember(n){ try { localStorage.setItem(POL_NAME_KEY, n); } catch(e){} }
function polShare(p){
  const x = p.later_paid_share;
  if(p.kind === 'payer_policy') return p.answered ? `<span title="Of the lines this payer answered for this code, the share it paid">${Math.round(100 * (x || 0))}% paid</span><div class="small muted">${Number(p.paid||0).toLocaleString()} of ${Number(p.answered).toLocaleString()} lines</div>` : '<span class="muted">–</span>';
  if(x == null) return '<span class="muted" title="Not known for this pattern: too few earlier denials like it, or a pattern too small for the run to size">–</span>';
  const pc = Math.round(100 * x);
  return `<span title="Of this pattern's earlier unpaid denials, the share the payer paid later">${pc}%</span>${pc === 0 ? '<div class="small muted">never paid later</div>' : (pc >= 20 ? '<div class="small ok">often paid later</div>' : '')}`;
}
function polWhen(p){
  if(p.decision === 'decide') return `<div class="small sev-medium">Waits for your decision${p.kind === 'payer_policy' ? ': out of Work now until then' : ''}</div>`;
  if(!p.decided_by) return '';
  return `<div class="small muted">${esc(p.decision === 'accept' ? 'Accepted' : 'Set to pursue')} by ${esc(p.decided_by)} on ${esc(longDate(String(p.decided_at||'').slice(0,10)))}${p.pending ? '; the next run applies it' : ''}</div>`;
}
function polWho(p){ return `${p.payer_group}, ${p.code ? p.code + (p.modifier ? '-' + p.modifier : '') : 'every code'}${p.reason ? ', ' + p.reason : ''}`; }

function polTotalsHtml(t, pats){
  const tot = t.totals || {}, dc = tot.decide || {}, c7 = tot.contractual || {};
  return `<b>${esc(pl(pats.length, 'pattern'))}</b> · <span title="Estimate: the shortfall against the payer's usual rate">${money(tot.cents)} written off in all (est.)</span> · <b>${money(tot.appealable_cents)}</b> still appealable (${esc(pl(tot.appealable, 'item'))})${tot.legal_only_cents ? `, of it ${money(tot.legal_only_cents)} only inside the payer's own limit` : ''} · ${esc(pl(tot.accepted||0, 'pattern'))} accepted`
    + (dc.patterns ? ` · <b class="sev-medium">${esc(pl(dc.patterns, 'code'))} a payer almost never pays wait for your decision</b> (${esc(pl(dc.items, 'denial'))}, ${money(dc.cents)} est.)` : '')
    + (c7.patterns ? ` · ${esc(pl(c7.patterns, 'reason'))} EMA posts as contractual, ${money(c7.written_off_cents)} written off that way (in no total)` : '');
}

async function policyTab(v){
  const t = await api('/api/ema/policy');
  const pats = t.patterns || [];
  v.innerHTML = `<div class="page-head"><h1 class="page-title">Write-off policy</h1><span class="muted small">What EMA writes off, by payer, code and reason: denials written off by its posting rules or later by a person, codes a payer almost never pays, and reasons EMA posts as contractual. Pursue keeps them in the work queue (or sends them there). Accept takes them off at the next run; they stay findable.</span></div>
    ${pats.length ? `<div class="panel" style="margin-top:0">
      <div class="wl-bar"><span class="small" id="polTotals">${polTotalsHtml(t, pats)}</span>
        <input id="polFind" data-f="q" type="search" maxlength="80" placeholder="Filter by payer, code or reason" aria-label="Filter the patterns by payer, code or reason" style="min-width:14rem">
        <label class="small">Your name <input id="polName" maxlength="40" value="${esc(polName())}" placeholder="kept with each decision" aria-label="Your name, kept with each decision" style="width:10rem"></label></div>
      ${polNarrow() ? `<div id="polTable">${pats.map((p,i)=>polCard(p, i)).join('')}</div>`
        : `<div class="scroll"><table class="small" id="polTable"><thead><tr><th>Payer</th><th>Code</th><th>Reason</th><th class="num">Items</th><th class="num" title="Estimate: the shortfall against the payer's usual rate">Dollars (est.)</th><th class="num" title="Open items inside the safe date to appeal, or the payer's own limit (one at a time)">Still appealable</th><th class="num">By EMA's rule</th><th class="num">Paid later before</th><th>Decision</th></tr></thead>
      <tbody>${pats.map((p,i)=>polRow(p, i)).join('')}</tbody></table></div>`}
      ${pats.length > POL_SHOW ? `<button class="ghost" id="polAll">Show all ${pats.length.toLocaleString()} patterns</button>` : ''}
      <div class="small muted" style="margin-top:6px">"By EMA's rule": items EMA wrote off the day the payer's answer posted, with nothing paid after. "Paid later before": how often this payer paid such a denial later, from the remittance archive; for a code the payer almost never pays, the share of its answered lines it paid.${t.run_at ? ` Counts as of the run on ${esc(longDate(String(t.run_at).slice(0,10)))}; a decision takes effect at the next run.` : ''}</div>
      <div class="todo-err sev-high small" id="polErr" role="alert"></div></div>`
    : `<div class="panel"><div class="empty">No denial written off in EMA is on file${t.run_at ? '' : ' yet: the run that reads EMA\'s Production report fills this page'}.</div></div>`}`;
  polBind(v, pats, t);
}

function polFindText(p){ return (p.payer_group + ' ' + p.code + ' ' + p.reason + ' ' + (p.reason_text||'') + ' ' + (POL_KIND[p.kind]||'')).toLowerCase(); }
function polReason(p){
  const label = p.kind === 'payer_policy' ? 'Payer policy' : (p.reason || '–');
  return `${esc(label)}${p.reason_text ? `<div class="small muted" title="${esc(p.reason_text)}">${esc(p.reason_text.length > 60 ? p.reason_text.slice(0, 57) + '...' : p.reason_text)}</div>` : ''}${p.kind === 'contractual' ? '<div class="small muted">posted by EMA as contractual</div>' : ''}`;
}
// the dollars of a row: a write-off pattern's shortfall (an estimate); a code the payer almost never pays, what its
// set-aside denials would bring (an estimate); a reason posted as contractual, what EMA wrote off that way (in no total)
function polMoney(p){
  if(p.kind === 'contractual') return `<span title="${esc(p.basis || '')}">${money(p.written_off_cents)}</span><div class="small muted">written off, in no total</div>`;
  return `<span title="${esc(p.basis || '')}">${money(p.cents)}</span>${polUpper(p)}`;
}
function polAppealable(p){
  if(p.kind === 'contractual') return p.inside_cents ? `<b title="What EMA wrote off as contractual on lines still inside the payer's window">${money(p.inside_cents)}</b><div class="small muted">inside a window</div>` : '<span class="muted">none</span>';
  if(p.appealable == null) return '<span class="muted" title="Worked out line by line once you choose Pursue">–</span>';
  if(!p.appealable) return '<span class="muted">none</span>';
  return `<b title="${esc(p.basis || '')}">${money(p.appealable_cents)}</b><div class="small muted">${esc(pl(p.appealable, 'item'))}${p.legal_only ? `; ${esc(pl(p.legal_only, 'item'))} only by the payer's own limit, one at a time` : ''}</div>`;
}
function polItemsCell(p, i){
  if(p.kind === 'payer_policy') return `<span title="Denials the run set aside: listed in the Worklist once you choose Pursue">${Number(p.items||0).toLocaleString()}</span>`;
  return p.items ? `<a href="#" class="polItems" data-i="${i}" title="List these items">${Number(p.items).toLocaleString()}</a>` : '0';
}
function polRow(p, i){
  return `<tr data-i="${i}"${i >= POL_SHOW ? ' hidden' : ''} data-find="${esc(polFindText(p))}">
    <td>${esc(p.payer_group || '–')}</td><td>${p.code ? esc(p.code) + (p.modifier ? '-' + esc(p.modifier) : '') : (p.kind === 'contractual' ? '<span class="muted">every code</span>' : '–')}</td><td>${polReason(p)}</td>
    <td class="num">${polItemsCell(p, i)}</td>
    <td class="num">${polMoney(p)}</td>
    <td class="num">${polAppealable(p)}</td>
    <td class="num">${p.auto == null ? '<span class="muted">–</span>' : Number(p.auto).toLocaleString()}</td><td class="num">${polShare(p)}</td>
    <td class="polDec" data-i="${i}">${polSegs(p, i)}</td></tr>
    <tr class="polItemsRow" data-for="${i}" hidden><td colspan="9"></td></tr>`;
}

// items EMA wrote off on a line no rate prices have no shortfall: what EMA wrote off on them (the charge less what was
// paid) is an upper bound, shown apart and never added to the dollars
function polUpper(p){
  if(!p.upper_n) return '';
  return `<div class="small muted" title="${esc(pl(p.upper_n, 'item'))} no rate prices: EMA wrote off up to this much on them, the charge less what was paid. Not in the dollars.">+ up to ${money(p.upper_cents)} not priced</div>`;
}
function polAmt(x){
  if(x.kind === 'contractual') return `<span title="Written off as contractual (EMA's postings)">${money2(x.written_off_cents)}</span>`;
  if(x.shortfall_cents == null && x.written_off_basis === 'charge_less_paid' && x.written_off_cents)
    return `<span title="No rate prices this line: EMA wrote off this much, the charge less what was paid">up to ${money2(x.written_off_cents)}</span>`;
  return `<span title="Estimate: the shortfall against the payer's usual rate">${money2(x.shortfall_cents)}</span>`;
}
// an item's window: the safe date, or, once that passed, the payer's own limit (it is still appealed, one at a time)
function polWindow(x){
  if(x.kind === 'contractual') return x.inside_cents ? `${money(x.inside_cents)} inside a window` : '<span class="muted">none inside a window</span>';
  if(!x.appeal_deadline) return '<span class="muted">no date</span>';
  const c = closesText(x.appeal_deadline);
  if(c.cls === 'muted' && x.legal_deadline && x.legal_deadline >= siteDay()) return `<span class="due-soon" title="The safe date passed; the payer's own limit is later: send it now, one at a time">payer's limit ${esc(longDate(x.legal_deadline))}</span>`;
  return `<span class="${c.cls}">${esc(c.text)}</span>`;
}

function polSegs(p, i){
  const acc = p.decision === 'accept', pur = p.decision === 'pursue';
  const tip = p.kind === 'payer_policy' ? ['Pursue: the next run lists every one of these denials in the Worklist', 'Accept: they stay written off, out of Work now']
    : p.kind === 'contractual' ? ['Pursue: have EMA post this reason as non-contractual, so its lines reach the denial queue', 'Accept: EMA is right to post it as contractual']
    : ['Pursue: the items stay in the work queue', 'Accept: the next run takes the items off the work queue; they stay findable'];
  return `<div class="segs" role="group" aria-label="Decision"><button class="seg ${pur?'on':''}" data-dec="pursue" data-i="${i}" aria-pressed="${pur}" title="${esc(tip[0])}">Pursue</button><button class="seg ${acc?'on':''}" data-dec="accept" data-i="${i}" aria-pressed="${acc}" title="${esc(tip[1])}">Accept</button></div>${polWhen(p)}`;
}
function polCard(p, i){
  const what = p.kind === 'contractual' ? `${money(p.written_off_cents)} written off as contractual (in no total)${p.inside_cents ? ` · <b>${money(p.inside_cents)}</b> inside a window` : ''}`
    : p.kind === 'payer_policy' ? `${esc(pl(p.items, 'denial'))} set aside, ${money(p.cents)} est.${p.answered ? ` · the payer paid ${Number(p.paid||0).toLocaleString()} of ${Number(p.answered).toLocaleString()} lines` : ''}`
    : `${esc(pl(p.items, 'item'))}, ${money(p.cents)} est.${p.upper_n ? ` (+ up to ${money(p.upper_cents)} not priced)` : ''}${p.appealable ? ` · <b>${money(p.appealable_cents)}</b> still appealable${p.legal_only ? ` (${esc(pl(p.legal_only, 'item'))} only by the payer's own limit)` : ''}` : ' · none still appealable'} · ${Number(p.auto||0).toLocaleString()} by EMA's rule${p.later_paid_share != null ? ` · ${Math.round(100*p.later_paid_share)}% paid later before` : ''}`;
  return `<div class="lab-card" data-i="${i}"${i >= POL_SHOW ? ' hidden' : ''} data-find="${esc(polFindText(p))}">
    <div><b>${esc(p.payer_group || '–')}</b> · ${p.code ? esc(p.code) + (p.modifier ? '-' + esc(p.modifier) : '') : 'every code'}${p.reason ? ' · ' + esc(p.reason) : ''}</div>
    ${p.reason_text ? `<div class="small muted">${esc(p.reason_text)}</div>` : ''}
    <div class="small" style="margin:4px 0">${what}</div>
    <div class="polDec" data-i="${i}">${polSegs(p, i)}</div>
    ${p.items && p.kind !== 'payer_policy' ? `<a href="#" class="polItems small" data-i="${i}">List the ${esc(pl(p.items, 'item'))}</a>` : ''}
    <div class="polItemsRow" data-for="${i}" hidden><div></div></div></div>`;
}

function polBind(v, pats, t){
  const find = v.querySelector('#polFind'), all = v.querySelector('#polAll'), nameBox = v.querySelector('#polName');
  let everyone = !all;
  // a typed filter searches every pattern; with no filter, the first POL_SHOW until "Show all"
  const apply = () => { const q = find ? find.value.trim().toLowerCase() : '';
    v.querySelectorAll('#polTable [data-find]').forEach(tr => { const show = q ? tr.dataset.find.includes(q) : (everyone || Number(tr.dataset.i) < POL_SHOW);
      tr.hidden = !show;
      const sub = v.querySelector(`.polItemsRow[data-for="${tr.dataset.i}"]`); if(sub && !show) sub.hidden = true; }); };
  if(find) find.oninput = apply;
  if(all) all.onclick = () => { everyone = true; all.remove(); apply(); };
  if(nameBox) nameBox.onchange = () => polRemember(nameBox.value.trim().slice(0, 40));
  v.querySelectorAll('.polItems').forEach(a=>a.onclick=e=>{ e.preventDefault(); const i = Number(a.dataset.i), row = v.querySelector(`.polItemsRow[data-for="${i}"]`);
    if(!row) return; if(!row.hidden){ row.hidden = true; return; } row.hidden = false; polItems(row.firstElementChild, pats[i], 1); });
  polBindSegs(v, v, pats, t);
}

// the Pursue and Accept buttons: Accept asks once what it does; Pursue asks nothing. The row changes in place (the filter,
// "Show all" and the scroll stay), and the totals line with it
function polBindSegs(v, el, pats, t){
  el.querySelectorAll('[data-dec]').forEach(b=>b.onclick=async()=>{
    const i = Number(b.dataset.i), p = pats[i], dec = b.dataset.dec, err = v.querySelector('#polErr');
    if(!p || p.decision === dec) return;
    if(dec === 'accept' && !confirm(polAcceptWords(p))) return;
    const nameBox = v.querySelector('#polName'), name = (nameBox ? nameBox.value : polName()).trim().slice(0, 40);
    polRemember(name);
    const cell = v.querySelector(`.polDec[data-i="${i}"]`);
    if(cell) cell.querySelectorAll('[data-dec]').forEach(x=>x.disabled = true);
    try {
      const r = await api('/api/ema/policy', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({pattern: p.pattern, decision: dec, name})});
      const was = p.decision;
      Object.assign(p, {decision: r.decision || dec, decided_by: r.by || name, decided_at: r.at || new Date().toISOString(), pending: true});
      const tot = t.totals || (t.totals = {});
      tot.accepted = (tot.accepted || 0) + (p.decision === 'accept' ? 1 : 0) - (was === 'accept' ? 1 : 0);
      if(was === 'decide' && p.kind === 'payer_policy' && tot.decide){ tot.decide = {patterns: tot.decide.patterns - 1, items: tot.decide.items - (p.items||0), cents: tot.decide.cents - (p.cents||0)}; }
      const c2 = v.querySelector(`.polDec[data-i="${i}"]`); if(c2){ c2.innerHTML = polSegs(p, i); polBindSegs(v, c2, pats, t); }
      const tl = v.querySelector('#polTotals'); if(tl) tl.innerHTML = polTotalsHtml(t, pats);
      if(err) err.textContent = '';
    } catch(e){ const c3 = v.querySelector(`.polDec[data-i="${i}"]`); if(c3) c3.querySelectorAll('[data-dec]').forEach(x=>x.disabled = false);
      if(err) err.textContent = 'Not saved: ' + (e.message || 'no answer from the site') + '. Try again.'; }
  });
}
function polAcceptWords(p){
  const who = polWho(p);
  if(p.kind === 'payer_policy') return `Accept this payer's policy (${who})?\n\n${pl(p.items, 'denial')} worth ${money(p.cents)} (est.) stay written off and out of Work now. Pursue brings them back.`;
  if(p.kind === 'contractual') return `Accept that EMA posts ${p.reason || 'this reason'} as contractual for ${p.payer_group}?\n\n${money(p.written_off_cents)} was written off that way. The item about it is closed at the next run as the policy. Pursue brings it back.`;
  return `Accept this pattern (${who})?\n\n${pl(p.items, 'item')} worth ${money(p.cents)} (est.)${p.appealable ? `, ${money(p.appealable_cents)} of it still appealable,` : ''} leave the work queue at the next run, as written off by the policy. They stay findable, and Pursue brings them back.`;
}

// one page of a pattern's items. Opening one sets the list the item's Next and "Mark sent and open the next" walk
async function polItems(cell, p, page){
  cell.innerHTML = '<div class="muted small">loading…</div>';
  let r; try { r = await api('/api/ema/policy/items?'+qs({pattern: p.pattern, page})); } catch(e){ cell.innerHTML = `<div class="small sev-high">The items did not come back: ${esc(e.message)}.</div>`; return; }
  const pages = Math.max(1, Math.ceil((r.count||0) / (r.size||POL_PAGE)));
  const rows = r.rows || [];
  const when = x => x.kind === 'contractual' ? esc(x.year || '') : esc(longDate(x.service_date));
  const code = x => x.kind === 'contractual' ? esc((x.top_codes || []).join(', ')) : esc(x.procedure_code||'');
  const list = polNarrow() ? rows.map(x=>`<div class="row check" tabindex="0" role="button" data-id="${esc(x.finding_id)}"><div><b>${polAmt(x)}</b> · ${code(x)} · ${when(x)}
      <div class="small">${esc(x.state_text||'')} · ${polWindow(x)} · ${esc(x.outcome_text||'')}</div></div></div>`).join('') : '';
  const head = r.note ? `<div class="small">${esc(r.note)}</div>`
    : `<div class="small"><b>${esc(pl(r.count, 'item'))}</b>${r.cents ? `, ${money(r.cents)} (est.)` : ''}${r.written_off_cents ? `${r.cents ? ' and' : ','} ${money(r.written_off_cents)} written off as contractual` : ''}. Open one to see its story and its next step.</div>`;
  cell.innerHTML = `${head}
    ${rows.length ? (list || `<table class="small"><thead><tr><th>Service</th><th>Code</th><th>Claim</th><th class="num">Amount</th><th>Window</th><th>State</th><th>What EMA did</th></tr></thead>
    <tbody>${rows.map(x=>`<tr class="row" tabindex="0" data-id="${esc(x.finding_id)}"><td class="day">${when(x)}</td><td>${code(x)}</td><td>${esc(x.patient_control_number||'')}</td>
      <td class="num">${polAmt(x)}</td><td>${polWindow(x)}</td><td>${esc(x.state_text||'')}</td><td>${esc(x.outcome_text||'')}</td></tr>`).join('')}</tbody></table>`) : ''}
    ${pages > 1 ? `<div class="pager"><button class="polPrev" ${page<=1?'disabled':''}>‹ prev</button><span>page ${page} of ${pages}</span><button class="polNext" ${page>=pages?'disabled':''}>next ›</button></div>` : ''}`;
  const ids = rows.map(x => x.finding_id), wl = 'policy:' + p.pattern;
  cell.querySelectorAll('.row[data-id]').forEach(tr=>{ const open = () => {
      state.worklist = ids.slice(); state.wlTotal = r.count; state.wlKey = wl;
      state.wlNext = async () => { if(state.wlKey !== wl) return 0; const at = (page - 1) * (r.size||POL_PAGE) + (state.worklist||[]).length;
        const more = await api('/api/ema/policy/items?'+qs({pattern: p.pattern, page: Math.floor(at / (r.size||POL_PAGE)) + 1}));
        if(state.wlKey !== wl) return 0; const seen = new Set(state.worklist||[]); const fresh = (more.rows||[]).map(x=>x.finding_id).filter(x=>!seen.has(x));
        state.worklist = (state.worklist||[]).concat(fresh); return fresh.length; };
      if(typeof openDetail === 'function') openDetail(tr.dataset.id); };
    tr.onclick = open; tr.onkeydown = e=>{ if(e.key==='Enter'||e.key===' '){ e.preventDefault(); open(); } }; });
  const pv = cell.querySelector('.polPrev'), nx = cell.querySelector('.polNext');
  if(pv) pv.onclick = () => polItems(cell, p, page-1);
  if(nx) nx.onclick = () => polItems(cell, p, page+1);
}
