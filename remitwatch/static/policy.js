// RemitWatch write-off policy page (#policy): the denials EMA's posting rules write off by themselves, by payer, code,
// modifier and reason, with the owner's decision on each: Pursue (the default: they stay in the work queue) or Accept
// (the next run takes them off the queue as written off by the owner's policy; they stay findable, and Pursue brings them
// back). Any signed-in person may decide, as with the hide rules; the name typed and the date are kept. Loaded after
// today.js; uses index.html's helpers ($, api, esc, money, money2, state, render, openDetail) and today.js's (longDate,
// pl). Names here start with pol (one global scope with the other scripts).

const POL_NAME_KEY = 'rw:policy:name';
const POL_PAGE = 50;

function polName(){ try { return localStorage.getItem(POL_NAME_KEY) || ''; } catch(e){ return ''; } }
// a phone (390 px): one card per pattern, so the decision buttons stay on screen
function polNarrow(){ return typeof window !== 'undefined' && window.innerWidth > 0 && window.innerWidth <= 640; }
function polRemember(n){ try { localStorage.setItem(POL_NAME_KEY, n); } catch(e){} }
function polShare(x){
  if(x == null) return '<span class="muted" title="Not enough earlier denials of this pattern to say">–</span>';
  const p = Math.round(100 * x);
  return `<span title="Of this pattern's earlier unpaid denials, the share the payer paid later">${p}%</span>${p === 0 ? '<div class="small muted">never paid later</div>' : (p >= 20 ? '<div class="small ok">often paid later</div>' : '')}`;
}
function polWhen(p){
  if(!p.decided_by) return '';
  return `<div class="small muted">${esc(p.decision === 'accept' ? 'Accepted' : 'Set to pursue')} by ${esc(p.decided_by)} on ${esc(longDate(String(p.decided_at||'').slice(0,10)))}${p.pending ? '; the next run applies it' : ''}</div>`;
}

async function policyTab(v){
  const t = await api('/api/ema/policy');
  const pats = t.patterns || [], tot = t.totals || {};
  v.innerHTML = `<div class="page-head"><h1 class="page-title">Write-off policy</h1><span class="muted small">Denials EMA's posting rules write off by themselves, grouped by payer, code and reason. Pursue keeps them in the work queue. Accept takes them off at the next run; they stay findable.</span></div>
    ${pats.length ? `<div class="panel" style="margin-top:0">
      <div class="wl-bar"><span class="small"><b>${esc(pl(pats.length, 'pattern'))}</b> · ${money(tot.cents)} written off · <b>${money(tot.appealable_cents)}</b> still inside a window (${esc(pl(tot.appealable, 'item'))}) · ${esc(pl(tot.accepted||0, 'pattern'))} accepted</span>
        <input id="polFind" type="search" maxlength="80" placeholder="Filter by payer, code or reason" aria-label="Filter the patterns by payer, code or reason" style="min-width:14rem"></div>
      ${polNarrow() ? `<div id="polTable">${pats.map((p,i)=>polCard(p, i)).join('')}</div>`
        : `<div class="scroll"><table class="small" id="polTable"><thead><tr><th>Payer</th><th>Code</th><th>Reason</th><th class="num">Items</th><th class="num">Dollars</th><th class="num">Still appealable</th><th class="num">By EMA's rule</th><th class="num">Paid later before</th><th>Decision</th></tr></thead>
      <tbody>${pats.map((p,i)=>polRow(p, i)).join('')}</tbody></table></div>`}
      <div class="small muted" style="margin-top:6px">"By EMA's rule": items EMA wrote off the day the payer's answer posted, with nothing paid after. "Paid later before": how often this payer paid such a denial later, from the remittance archive.${t.run_at ? ` Counts as of the run on ${esc(longDate(String(t.run_at).slice(0,10)))}; a decision takes effect at the next run.` : ''}</div>
      <div class="todo-err sev-high small" id="polErr" role="alert"></div></div>`
    : `<div class="panel"><div class="empty">No denial written off by EMA's posting rules is on file${t.run_at ? '' : ' yet: the run that reads EMA\'s Production report fills this page'}.</div></div>`}`;
  polBind(v, pats);
}

function polRow(p, i){
  const reason = `${esc(p.reason || '–')}${p.reason_text ? `<div class="small muted" title="${esc(p.reason_text)}">${esc(p.reason_text.length > 60 ? p.reason_text.slice(0, 57) + '...' : p.reason_text)}</div>` : ''}`;
  return `<tr data-i="${i}" data-find="${esc((p.payer_group + ' ' + p.code + ' ' + p.reason + ' ' + (p.reason_text||'')).toLowerCase())}">
    <td>${esc(p.payer_group || '–')}</td><td>${esc(p.code || '–')}${p.modifier ? '-' + esc(p.modifier) : ''}</td><td>${reason}</td>
    <td class="num">${p.items ? `<a href="#" class="polItems" data-i="${i}" title="List these items">${Number(p.items).toLocaleString()}</a>` : '0'}</td>
    <td class="num">${money(p.cents)}${polUpper(p)}</td>
    <td class="num">${p.appealable ? `<b>${money(p.appealable_cents)}</b><div class="small muted">${esc(pl(p.appealable, 'item'))}</div>` : '<span class="muted">none</span>'}</td>
    <td class="num">${Number(p.auto||0).toLocaleString()}</td><td class="num">${polShare(p.later_paid_share)}</td>
    <td>${polSegs(p, i)}</td></tr>
    <tr class="polItemsRow" data-for="${i}" hidden><td colspan="9"></td></tr>`;
}

// items EMA wrote off on a line no rate prices have no shortfall: what EMA wrote off on them (the charge less what was
// paid) is an upper bound, shown apart and never added to the dollars
function polUpper(p){
  if(!p.upper_n) return '';
  return `<div class="small muted" title="${esc(pl(p.upper_n, 'item'))} no rate prices: EMA wrote off up to this much on them, the charge less what was paid. Not in the dollars.">+ up to ${money(p.upper_cents)} not priced</div>`;
}
function polAmt(x){
  if(x.shortfall_cents == null && x.written_off_basis === 'charge_less_paid' && x.written_off_cents)
    return `<span title="No rate prices this line: EMA wrote off this much, the charge less what was paid">up to ${money2(x.written_off_cents)}</span>`;
  return money2(x.shortfall_cents);
}

function polSegs(p, i){
  const acc = p.decision === 'accept';
  return `<div class="segs" role="group" aria-label="Decision"><button class="seg ${acc?'':'on'}" data-dec="pursue" data-i="${i}" aria-pressed="${!acc}">Pursue</button><button class="seg ${acc?'on':''}" data-dec="accept" data-i="${i}" aria-pressed="${acc}">Accept</button></div>${polWhen(p)}`;
}
function polCard(p, i){
  return `<div class="lab-card" data-i="${i}" data-find="${esc((p.payer_group + ' ' + p.code + ' ' + p.reason + ' ' + (p.reason_text||'')).toLowerCase())}">
    <div><b>${esc(p.payer_group || '–')}</b> · ${esc(p.code || '–')}${p.modifier ? '-' + esc(p.modifier) : ''} · ${esc(p.reason || '–')}</div>
    ${p.reason_text ? `<div class="small muted">${esc(p.reason_text)}</div>` : ''}
    <div class="small" style="margin:4px 0">${esc(pl(p.items, 'item'))}, ${money(p.cents)}${p.upper_n ? ` (+ up to ${money(p.upper_cents)} not priced)` : ''}${p.appealable ? ` · <b>${money(p.appealable_cents)}</b> still appealable` : ' · none still appealable'} · ${Number(p.auto||0).toLocaleString()} by EMA's rule${p.later_paid_share != null ? ` · ${Math.round(100*p.later_paid_share)}% paid later before` : ''}</div>
    ${polSegs(p, i)}
    ${p.items ? `<a href="#" class="polItems small" data-i="${i}">List the ${esc(pl(p.items, 'item'))}</a>` : ''}
    <div class="polItemsRow" data-for="${i}" hidden><div></div></div></div>`;
}

function polBind(v, pats){
  const find = v.querySelector('#polFind');
  if(find) find.oninput = () => { const q = find.value.trim().toLowerCase();
    v.querySelectorAll('#polTable [data-find]').forEach(tr => { const show = !q || tr.dataset.find.includes(q); tr.hidden = !show;
      const sub = v.querySelector(`.polItemsRow[data-for="${tr.dataset.i}"]`); if(sub && !show) sub.hidden = true; }); };
  v.querySelectorAll('.polItems').forEach(a=>a.onclick=e=>{ e.preventDefault(); const i = Number(a.dataset.i), row = v.querySelector(`.polItemsRow[data-for="${i}"]`);
    if(!row) return; if(!row.hidden){ row.hidden = true; return; } row.hidden = false; polItems(row.firstElementChild, pats[i], 1); });
  v.querySelectorAll('[data-dec]').forEach(b=>b.onclick=async()=>{
    const p = pats[Number(b.dataset.i)], dec = b.dataset.dec, err = v.querySelector('#polErr');
    if(!p || p.decision === dec) return;
    if(dec === 'accept' && !confirm(`Accept this pattern (${p.payer_group}, ${p.code}${p.modifier?'-'+p.modifier:''}, ${p.reason})?\n\n${pl(p.items, 'item')} worth ${money(p.cents)}${p.appealable ? `, ${money(p.appealable_cents)} of it still inside a window,` : ''} leave the work queue at the next run, as written off by the owner's policy. They stay findable, and Pursue brings them back.`)) return;
    let name = polName();
    const typed = prompt('Your name, kept with the decision (optional):', name);
    if(typed === null) return;
    name = typed.trim().slice(0, 40); polRemember(name);
    v.querySelectorAll('[data-dec]').forEach(x=>x.disabled = true);
    try { await api('/api/ema/policy', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({pattern: p.pattern, decision: dec, name})});
      render(); }
    catch(e){ v.querySelectorAll('[data-dec]').forEach(x=>x.disabled = false); if(err) err.textContent = 'Not saved: ' + (e.message || 'no answer from the site') + '. Try again.'; }
  });
}

async function polItems(cell, p, page){
  cell.innerHTML = '<div class="muted small">loading…</div>';
  let r; try { r = await api('/api/ema/policy/items?'+qs({pattern: p.pattern, page})); } catch(e){ cell.innerHTML = `<div class="small sev-high">The items did not come back: ${esc(e.message)}.</div>`; return; }
  const pages = Math.max(1, Math.ceil((r.count||0) / (r.size||POL_PAGE)));
  const list = polNarrow() ? (r.rows||[]).map(x=>{ const c = closesText(x.appeal_deadline); return `<div class="row check" tabindex="0" role="button" data-id="${esc(x.finding_id)}"><div><b>${polAmt(x)}</b> · ${esc(x.procedure_code||'')} · ${esc(longDate(x.service_date))}
      <div class="small">${esc(x.state_text||'')} · ${x.appeal_deadline ? `<span class="${c.cls}">${esc(c.text)}</span>` : 'no date'} · ${esc(x.outcome_text||'')}</div></div></div>`; }).join('') : '';
  cell.innerHTML = `<div class="small"><b>${esc(pl(r.count, 'item'))}</b>, ${money(r.cents)}. Open one to see its story and its next step.</div>
    ${list || `<table class="small"><thead><tr><th>Service</th><th>Code</th><th>Claim</th><th class="num">Amount</th><th>Window</th><th>State</th><th>What EMA did</th></tr></thead>
    <tbody>${(r.rows||[]).map(x=>{ const c = closesText(x.appeal_deadline); return `<tr class="row" tabindex="0" data-id="${esc(x.finding_id)}"><td class="day">${esc(longDate(x.service_date))}</td><td>${esc(x.procedure_code||'')}</td><td>${esc(x.patient_control_number||'')}</td>
      <td class="num">${polAmt(x)}</td><td class="${c.cls}">${x.appeal_deadline ? esc(c.text) : '<span class="muted">no date</span>'}</td><td>${esc(x.state_text||'')}</td><td>${esc(x.outcome_text||'')}</td></tr>`; }).join('')}</tbody></table>`}
    ${pages > 1 ? `<div class="pager"><button class="polPrev" ${page<=1?'disabled':''}>‹ prev</button><span>page ${page} of ${pages}</span><button class="polNext" ${page>=pages?'disabled':''}>next ›</button></div>` : ''}`;
  cell.querySelectorAll('.row[data-id]').forEach(tr=>{ const open = () => { if(typeof openDetail === 'function') openDetail(tr.dataset.id); };
    tr.onclick = open; tr.onkeydown = e=>{ if(e.key==='Enter'||e.key===' '){ e.preventDefault(); open(); } }; });
  const pv = cell.querySelector('.polPrev'), nx = cell.querySelector('.polNext');
  if(pv) pv.onclick = () => polItems(cell, p, page-1);
  if(nx) nx.onclick = () => polItems(cell, p, page+1);
}
