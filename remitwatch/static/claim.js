// RemitWatch: one claim, all of it. "Find a claim" in the header takes a claim ID (any submission of the bill, or the start
// of the ID), the payer's claim number however it is typed or, with names shown, a patient; it opens the whole bill in the
// drawer: every remittance for every submission in the payer's order, with every line, adjustment and remark in words;
// the items found on it (its own, the takebacks and routine-rate patterns that name it); the money taken back on it; the
// EMA rows, also of submissions no payer has answered yet. Every item's drawer links here. The claim ID travels in a
// header, never in an address. Loaded after desk.js; uses index.html's and today.js's helpers.

const CLAIM_FREQ = {'1':'original', '7':'replacement (corrected claim)', '8':'void'};
const PLB_REASON = {WO:'Taken back (overpayment recovery)', FB:'Forwarding balance', L6:'Interest', '72':'Authorized return', CS:'Adjustment', AH:'Origination fee', J1:'Non-reimbursable'};

function searchHeader(text){
  let sent = ''; try { sent = encodeURIComponent(String(text||'').slice(0,200)); } catch(e){ sent = encodeURIComponent(String(text||'').replace(/[\uD800-\uDFFF]/g,'').slice(0,200)); }
  return {cache:'no-store', headers:{'X-Search': sent}};       // the text travels in a header, never in the address
}
function claimHeader(id){
  let sent = ''; try { sent = encodeURIComponent(String(id||'').slice(0,60)); } catch(e){ sent = ''; }
  return {cache:'no-store', headers:{'X-Claim': sent}};
}
function claimShell(title, sub, body){
  const d = $('#drawer'); d.classList.add('open'); d.inert = false;
  d.innerHTML = `<div class="dr-head"><div><h2>${title}</h2>${sub ? `<div class="small muted">${sub}</div>` : ''}</div><button id="closeD" aria-label="Close">✕</button></div>${body}`;
  d.querySelector('#closeD').onclick = closeDetail;
  d.querySelector('#closeD').focus({preventScroll:true});
  return d;
}
async function findClaim(text){
  text = String(text||'').trim();
  if(text.length < 3){ claimShell('Find a claim', '', '<div class="note">Type at least three characters of the claim ID (as EMA shows it) or the payer\'s claim number.</div>'); return; }
  const from = document.activeElement; if(from && from.id === 'claimFind') state.detailFrom = from;
  claimShell('Find a claim', '', '<div class="muted">Looking…</div>');
  let r; try { r = await api('/api/claims/search', searchHeader(text)); } catch(e){ claimShell('Find a claim', '', `<div class="note">Not found: ${esc(e.message)}</div>`); return; }
  if(r.items.length === 1 || (r.items[0] && r.items[0].exact)) return openClaim(r.items[0].bill);
  if(!r.items.length){ claimShell('Find a claim', '', `<div class="note">Nothing in the remittance files or the EMA exports matches “${esc(text)}”. Check the claim ID in EMA, or try the payer's claim number from the payer's portal.</div>`); return; }
  const d = claimShell('Find a claim', r.more ? `More than 25 claims start with “${esc(text)}”: type more of the ID. The 25 most recently paid of those found:` : `${pl(r.items.length, 'claim')} match`,
    `<div class="scroll"><table class="small"><thead><tr><th>Claim</th><th>Patient</th><th>Payer</th><th>Last paid</th><th class="num">Remittances</th><th class="num">Open items</th></tr></thead>
     <tbody>${r.items.map(x=>`<tr class="row" tabindex="0" data-bill="${esc(x.bill)}"><td class="code"><b>${esc(x.bill)}</b>${x.claim_ids.length>1?`<div class="small muted">${pl(x.claim_ids.length,'submission')}</div>`:''}</td><td>${esc(x.patient||'')}</td><td>${esc(x.payers.join(', ') || 'no remittance yet')}</td><td class="day">${x.last_payment ? esc(longDate(x.last_payment)) : '–'}</td><td class="num">${num(x.remits)}</td><td class="num">${x.open_findings ? num(x.open_findings) : '–'}</td></tr>`).join('')}</tbody></table></div>`);
  d.querySelectorAll('tr[data-bill]').forEach(tr=>{ tr.onclick = () => openClaim(tr.dataset.bill); tr.onkeydown = e => { if(e.key==='Enter'){ e.preventDefault(); openClaim(tr.dataset.bill); } }; });
}
function adjText(list){ return (list||[]).map(a=>`<div>${esc(a.group_code)}-${esc(a.reason_code)} ${money2(a.amount_cents)} <span class="muted">${esc(a.text||'')}</span></div>`).join(''); }
function remText(list){ return (list||[]).map(x=>`<div class="muted">${esc(x.code)} ${esc(x.text||'')}</div>`).join(''); }
function claimFacts(r){
  const svc = r.statement_from ? (r.statement_to && r.statement_to !== r.statement_from ? `${longDate(r.statement_from)} to ${longDate(r.statement_to)}` : longDate(r.statement_from)) : '';
  return [r.received_date ? 'received '+longDate(r.received_date) : '', svc ? 'service '+svc : '', CLAIM_FREQ[r.claim_frequency_code] ? CLAIM_FREQ[r.claim_frequency_code] : '',
    r.rendering_provider_name ? 'rendered by '+String(r.rendering_provider_name).replace(/\s+/g,' ').trim() : '', r.sent_on_to ? 'sent on to '+r.sent_on_to : '',
    r.payer_phone ? 'payer phone '+r.payer_phone : '', r.payer_url || ''].filter(Boolean).map(esc).join(' · ');
}
async function openClaim(id, backTo){
  const d = $('#drawer'); d.classList.add('open'); d.inert = false; d.innerHTML = '<div class="muted">loading…</div>';
  let c; try { c = await api('/api/claims/view', claimHeader(id)); }
  catch(e){ claimShell('Claim', '', `<div class="note">${esc(e.message)}</div>`); return; }
  const b = state.brief || {labels:{}};
  // which lines and remittances the items were found on
  const itemsOnLine = {};
  c.findings.forEach(f => (f.line_key ? [f.line_key] : (f.line_keys||[])).forEach(k => { (itemsOnLine[k] = itemsOnLine[k] || []).push(f); }));
  const open = c.findings.filter(f => ['open','in_progress'].includes(f.status)).length;
  const due = f => f.via === 'pattern' ? f.claim_deadline : f.appeal_deadline;
  const itemRow = f => { const d0 = due(f), ct = closesText(d0), live = ['open','in_progress'].includes(f.status);
    const where = f.via === 'takeback' ? `taken back ${f.remit_date ? longDate(f.remit_date) : ''}${f.remit_payer ? ' by '+f.remit_payer : ''}`
      : f.via === 'pattern' ? `this claim's ${pl((f.line_keys||[]).length, 'line')} in the payer's routine lower rate`
      : `${f.remit_date ? 'on the remittance of '+longDate(f.remit_date) : ''}${f.remit_payer ? ' from '+f.remit_payer : ''}`;
    return `<tr class="row ${f.finding_id===backTo?'picked':''}" tabindex="0" data-fid="${esc(f.finding_id)}"><td>${esc(labelOf(b, f.signal).label || f.signal)}<div class="small muted">${esc([f.procedure_code, where].filter(Boolean).join(' · '))}${f.stale?' · no longer found by the current rules':''}</div></td>
      <td class="day">${live && d0 ? `<span class="${ct.cls}">${esc(ct.text)}</span>` : (d0 ? `<span class="muted">${esc(longDate(d0))}</span>` : '<span class="muted">no date</span>')}</td>
      <td><span class="st st-${esc(f.status)}">${esc(STATUS_WORDS[f.status]||f.status)}</span></td><td class="num">${(f.via==='pattern'?f.claim_cents:f.shortfall_cents) ? money2(f.via==='pattern'?f.claim_cents:f.shortfall_cents) : '–'}</td></tr>`; };
  const remitHtml = (r, i) => `<details class="dfold" ${i===c.remits.length-1?'open':''}><summary>${esc(longDate(r.payment_date))} · ${esc(r.payer_name||'')} · <b>${esc(r.status_words)}</b> · charged ${money2(r.charge_cents)} · paid ${money2(r.paid_cents)} · ${esc(r.patient_control_number)}</summary>
      <div class="small muted" style="margin:4px 0">${claimFacts(r)}</div>
      <div class="small muted" style="margin:0 0 4px">Payer claim number ${esc(r.payer_claim_control_number||'–')} · ${esc(r.payment_method||'')} ${r.trace_number?'trace '+esc(r.trace_number):''} · patient owes ${money2(r.patient_responsibility_cents)}${r.rendering_provider_npi?' · NPI '+esc(r.rendering_provider_npi):''} · file ${esc(r.source_name||'')}</div>
      ${(r.adjustments||[]).length || (r.remarks||[]).length ? `<div class="small"><b>On the whole claim:</b> ${adjText(r.adjustments)}${remText(r.remarks)}</div>` : ''}
      ${(r.lines||[]).length ? `<div class="scroll"><table class="small"><thead><tr><th>#</th><th>Date</th><th>Code</th><th>Billed as</th><th class="num">Units</th><th class="num">Charge</th><th class="num">Allowed</th><th class="num">Paid</th><th>Adjustments and remarks</th></tr></thead>
      <tbody>${r.lines.map(l=>`<tr ${itemsOnLine[l.line_key]?'class="has-item"':''}><td>${esc(l.line_seq)}</td><td class="day">${esc(l.service_date||'')}</td><td class="code">${esc(l.procedure_code||'')}${mods(l.modifiers)}</td><td class="code">${esc(l.original_procedure_code||'')}${l.original_procedure_code?mods(l.original_modifiers):''}</td><td class="num">${esc(l.paid_units ?? '')}${l.original_units && l.original_units!==l.paid_units?` <span class="muted">of ${esc(l.original_units)}</span>`:''}</td><td class="num">${money2(l.charge_cents)}</td><td class="num">${money2(l.allowed_cents)}</td><td class="num">${money2(l.paid_cents)}</td><td>${adjText(l.adjustments)}${remText(l.remarks)}${(itemsOnLine[l.line_key]||[]).map(f=>`<div><a href="#" class="line-item" data-fid="${esc(f.finding_id)}">Item: ${esc(shortOf(b, f.signal))}</a></div>`).join('')}</td></tr>`).join('')}</tbody></table></div>`
        : '<div class="small muted">No service lines on this remittance: the payer answered for the whole claim.</div>'}
    </details>`;
  const unanswered = (c.claim_ids||[]).filter(x => !(c.answered||[]).includes(x));
  const emaOnly = !c.remits.length;
  const lastEma = (c.ema||[]).slice().sort((x,y)=>String(x.posted_date||x.transaction_date||'').localeCompare(String(y.posted_date||y.transaction_date||''))).pop();
  const body = `${backTo ? `<button class="ghost" id="claimBack">‹ Back to the item</button>` : ''}
    ${emaOnly ? `<div class="note">No remittance in the files yet${lastEma && lastEma.claim_status ? `; EMA says ${esc(lastEma.claim_status)}${lastEma.posted_date||lastEma.transaction_date ? ' as of '+esc(longDate(String(lastEma.posted_date||lastEma.transaction_date).slice(0,10))) : ''}` : ''}.</div>` : ''}
    ${unanswered.length && !emaOnly ? `<div class="small muted">In EMA with no remittance in the files: ${unanswered.map(esc).join(', ')}.</div>` : ''}
    <h3 class="small">Items found on this claim${open ? ` (${num(open)} open)` : ''}</h3>
    ${c.findings.length ? `<div class="scroll"><table class="small"><tbody>${c.findings.map(itemRow).join('')}</tbody></table></div>` : '<div class="small muted">Nothing on this claim needed a person.</div>'}
    ${(c.taken_back||[]).length ? `<h3 class="small">Money taken back or held over that names this claim</h3><div class="scroll"><table class="small"><thead><tr><th>Date</th><th>Payer</th><th>What</th><th class="num">Amount</th><th>Reference</th></tr></thead>
      <tbody>${c.taken_back.map(p=>`<tr><td class="day">${esc(longDate(p.payment_date))}</td><td>${esc(p.payer_name||'')}</td><td>${esc(PLB_REASON[p.reason_code]||p.reason_code)}</td><td class="num">${money2(p.amount_cents)}</td><td class="small muted code">${esc(p.reference||'')}${p.trace_number?' · trace '+esc(p.trace_number):''}</td></tr>`).join('')}</tbody></table></div>` : ''}
    ${emaOnly ? '' : `<div class="actions" style="margin-top:8px"><button id="claimCsv">Export every line (CSV)</button></div>
    <h3 class="small">Every remittance, in the payer's order</h3>
    ${c.remits.map(remitHtml).join('')}`}
    ${c.ema.length ? `<details class="dfold" ${emaOnly?'open':''}><summary>ModMed EMA rows for this claim (${num(c.ema.length)})</summary><div class="scroll"><table class="small"><thead><tr><th>Family</th><th>Claim</th><th>Date</th><th>CPT</th><th class="num">Charge</th><th class="num">Paid</th><th class="num">Adjusted</th><th>Status and handling</th><th>Posted</th></tr></thead>
      <tbody>${c.ema.map(e=>`<tr><td>${esc(e.family)}${e.activity_type?' · '+esc(e.activity_type):''}</td><td class="code">${esc(e.claim_id)}</td><td class="day">${esc(e.service_date||'')}</td><td class="code">${esc(e.cpt||'')}${mods(e.modifiers)}</td><td class="num">${money2(e.charges_cents)}</td><td class="num">${money2(e.payments_cents)}</td><td class="num">${money2(e.adjustments_cents)}</td><td>${esc([e.claim_status, e.carc, e.adjustment_status, e.denial_category].filter(Boolean).join(' · '))}</td><td class="day">${esc(String(e.posted_date||e.transaction_date||'').slice(0,10))}</td></tr>`).join('')}</tbody></table></div></details>`
      : (emaOnly ? '' : '<div class="small muted" style="margin-top:8px">No EMA export rows for this claim.</div>')}`;
  const sub = `${esc(c.patient||'')}${c.member_id?' · member ID '+esc(c.member_id):''}${c.patient?' · ':''}${pl(c.remits.length, 'remittance')}${(c.claim_ids||[]).length>1?' · submissions '+c.claim_ids.map(esc).join(', '):''}`;
  const dd = claimShell(`Claim ${esc(c.bill)}`, sub, body);
  const bk = dd.querySelector('#claimBack'); if(bk) bk.onclick = () => openDetail(backTo);
  // an item opened from here keeps the walk of the list behind: the drawer walks only an item that is on that list
  dd.querySelectorAll('tr[data-fid], a.line-item').forEach(el=>{ const go = e => { if(e) e.preventDefault(); openDetail(el.dataset.fid); };
    el.onclick = go; el.onkeydown = e => { if(e.key==='Enter'){ go(e); } }; });
  const csv = dd.querySelector('#claimCsv'); if(csv) csv.onclick = () => claimCsv(c);
}
// every remittance as a claim row (its claim-level adjustments and remarks), then its lines; a missing amount is blank
function claimCsv(c){
  const cell = x => { let s = x == null ? '' : String(x); if(/^[=+\-@\t\r]/.test(s) && isNaN(Number(s))) s = "'" + s; return /[",\n\r]/.test(s) ? '"'+s.replace(/"/g,'""')+'"' : s; };
  const amt = v => v == null ? '' : (v/100).toFixed(2);
  const adj = list => (list||[]).map(a=>`${a.group_code}-${a.reason_code} ${amt(a.amount_cents)}`).join('; ');
  const rem = list => (list||[]).map(x=>x.code).join(' ');
  const head = ['claim_id','payer_claim_number','payment_date','payer','status','trace','level','line','service_date','code','modifiers','billed_code','units','charge','allowed','paid','patient_owes','adjustments','remarks'];
  const out = [head.join(',')];
  c.remits.forEach(r => {
    out.push([r.patient_control_number, r.payer_claim_control_number, r.payment_date, r.payer_name, r.status_words, r.trace_number, 'claim', '', '', '', '', '', '',
      amt(r.charge_cents), '', amt(r.paid_cents), amt(r.patient_responsibility_cents), adj(r.adjustments), rem(r.remarks)].map(cell).join(','));
    (r.lines||[]).forEach(l => out.push([r.patient_control_number, r.payer_claim_control_number, r.payment_date, r.payer_name, r.status_words, r.trace_number, 'line', l.line_seq, l.service_date,
      l.procedure_code, (()=>{ try { return JSON.parse(l.modifiers||'[]').join(' '); } catch(e){ return ''; } })(), l.original_procedure_code, l.paid_units,
      amt(l.charge_cents), amt(l.allowed_cents), amt(l.paid_cents), '', adj(l.adjustments), rem(l.remarks)].map(cell).join(',')));
  });
  (c.taken_back||[]).forEach(p => out.push(['', '', p.payment_date, p.payer_name, PLB_REASON[p.reason_code]||p.reason_code, p.trace_number, 'taken back', '', '', '', '', '', '', '', '', amt(-Math.abs(p.amount_cents||0)), '', p.reference, ''].map(cell).join(',')));
  const url = URL.createObjectURL(new Blob([out.join('\n')], {type:'text/csv'})), a = document.createElement('a');
  a.href = url; a.download = `remitwatch-claim-${c.bill}.csv`; document.body.appendChild(a); a.click(); a.remove(); setTimeout(()=>URL.revokeObjectURL(url), 10000);
}
