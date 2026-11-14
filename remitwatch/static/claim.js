// RemitWatch: one claim, all of it. "Find a claim" in the header takes a claim ID (any submission of the bill, or the start
// of the ID), the payer's claim number or, with names shown, a patient; it opens the whole bill in the drawer: every
// remittance for every submission, oldest first, with every line, adjustment and remark in words, the items found on it
// and the EMA rows. Every item's drawer links here too. Loaded after desk.js; uses index.html's and today.js's helpers.

function searchHeader(text){
  let sent = ''; try { sent = encodeURIComponent(String(text||'').slice(0,200)); } catch(e){ sent = encodeURIComponent(String(text||'').replace(/[\uD800-\uDFFF]/g,'').slice(0,200)); }
  return {cache:'no-store', headers:{'X-Search': sent}};       // the text travels in a header, never in the address
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
  if(r.items.length === 1) return openClaim(r.items[0].bill);
  if(!r.items.length){ claimShell('Find a claim', '', `<div class="note">No remittance in the files has a claim matching “${esc(text)}”. Check the claim ID in EMA; a claim the payer has not answered yet has no remittance here.</div>`); return; }
  const d = claimShell('Find a claim', `${pl(r.items.length, 'claim')} match${r.more ? ' (the first 25)' : ''}`,
    `<table class="small"><thead><tr><th>Claim</th><th>Patient</th><th>Payer</th><th>Last paid</th><th class="num">Remittances</th><th class="num">Open items</th></tr></thead>
     <tbody>${r.items.map(x=>`<tr class="row" tabindex="0" data-bill="${esc(x.bill)}"><td><b>${esc(x.bill)}</b>${x.claim_ids.length>1?`<div class="small muted">${pl(x.claim_ids.length,'submission')}</div>`:''}</td><td>${esc(x.patient||'')}</td><td>${esc(x.payers.join(', '))}</td><td>${esc(longDate(x.last_payment))}</td><td class="num">${num(x.remits)}</td><td class="num">${x.open_findings ? num(x.open_findings) : '–'}</td></tr>`).join('')}</tbody></table>`);
  d.querySelectorAll('tr[data-bill]').forEach(tr=>{ tr.onclick = () => openClaim(tr.dataset.bill); tr.onkeydown = e => { if(e.key==='Enter'){ e.preventDefault(); openClaim(tr.dataset.bill); } }; });
}
function adjText(list){
  return (list||[]).map(a=>`<div>${esc(a.group_code)}-${esc(a.reason_code)} ${money2(a.amount_cents)} <span class="muted">${esc(a.text||'')}</span></div>`).join('');
}
function remText(list){ return (list||[]).map(x=>`<div class="muted">${esc(x.code)} ${esc(x.text||'')}</div>`).join(''); }
async function openClaim(bill, backTo){
  const d = $('#drawer'); d.classList.add('open'); d.inert = false; d.innerHTML = '<div class="muted">loading…</div>';
  let c; try { c = await api('/api/claims/'+encodeURIComponent(bill)); }
  catch(e){ claimShell(`Claim ${esc(bill)}`, '', `<div class="note">${esc(e.message)}</div>`); return; }
  const b = state.brief || {labels:{}};
  const back = backTo ? `<button class="ghost" id="claimBack">‹ Back to the item</button>` : '';
  const many = c.claim_ids.length > 1;
  const body = `${back}
    <h3 class="small">Items found on this claim</h3>
    ${c.findings.length ? `<table class="small"><tbody>${c.findings.map(f=>`<tr class="row" tabindex="0" data-fid="${esc(f.finding_id)}"><td>${esc(labelOf(b, f.signal).label || f.signal)}<div class="small muted">${esc(f.procedure_code||'')}${f.appeal_deadline?' · file by '+esc(longDate(f.appeal_deadline)):''}${f.stale?' · no longer found by the current rules':''}</div></td><td><span class="st st-${esc(f.status)}">${esc(STATUS_WORDS[f.status]||f.status)}</span></td><td class="num">${f.shortfall_cents ? money2(f.shortfall_cents) : '–'}</td></tr>`).join('')}</tbody></table>`
      : '<div class="small muted">Nothing on this claim needed a person.</div>'}
    <div class="actions" style="margin-top:8px"><button id="claimCsv">Export every line (CSV)</button></div>
    <h3 class="small">Every remittance, oldest first</h3>
    ${c.remits.map((r,i)=>`<details class="dfold" ${i===c.remits.length-1?'open':''}><summary>${esc(longDate(r.payment_date))} · ${esc(r.payer_name||'')} · <b>${esc(r.status_words)}</b> · charged ${money2(r.charge_cents)} · paid ${money2(r.paid_cents)}${many?' · '+esc(r.patient_control_number):''}</summary>
      <div class="small muted" style="margin:4px 0">Payer claim number ${esc(r.payer_claim_control_number||'–')} · ${esc(r.payment_method||'')} ${r.trace_number?'trace '+esc(r.trace_number):''} · patient owes ${money2(r.patient_responsibility_cents)}${r.rendering_provider_npi?' · rendering NPI '+esc(r.rendering_provider_npi):''} · file ${esc(r.source_name||'')}</div>
      ${(r.adjustments||[]).length || (r.remarks||[]).length ? `<div class="small"><b>On the whole claim:</b> ${adjText(r.adjustments)}${remText(r.remarks)}</div>` : ''}
      <div class="scroll"><table class="small"><thead><tr><th>#</th><th>Date</th><th>Code</th><th>Billed as</th><th class="num">Units</th><th class="num">Charge</th><th class="num">Allowed</th><th class="num">Paid</th><th>Adjustments and remarks</th></tr></thead>
      <tbody>${(r.lines||[]).map(l=>`<tr><td>${esc(l.line_seq)}</td><td class="day">${esc(l.service_date||'')}</td><td>${esc(l.procedure_code||'')}${mods(l.modifiers)}</td><td>${esc(l.original_procedure_code||'')}${l.original_procedure_code?mods(l.original_modifiers):''}</td><td class="num">${esc(l.paid_units ?? '')}${l.original_units && l.original_units!==l.paid_units?` <span class="muted">of ${esc(l.original_units)}</span>`:''}</td><td class="num">${money2(l.charge_cents)}</td><td class="num">${money2(l.allowed_cents)}</td><td class="num">${money2(l.paid_cents)}</td><td>${adjText(l.adjustments)}${remText(l.remarks)}</td></tr>`).join('')}</tbody></table></div>
    </details>`).join('')}
    ${c.ema.length ? `<details class="dfold"><summary>ModMed EMA rows for this claim (${num(c.ema.length)})</summary><div class="scroll"><table class="small"><thead><tr><th>Family</th><th>Claim</th><th>Date</th><th>CPT</th><th class="num">Charge</th><th class="num">Paid</th><th class="num">Adjusted</th><th>Status and handling</th></tr></thead>
      <tbody>${c.ema.map(e=>`<tr><td>${esc(e.family)}${e.activity_type?' · '+esc(e.activity_type):''}</td><td>${esc(e.claim_id)}</td><td class="day">${esc(e.service_date||'')}</td><td>${esc(e.cpt||'')}${mods(e.modifiers)}</td><td class="num">${money2(e.charges_cents)}</td><td class="num">${money2(e.payments_cents)}</td><td class="num">${money2(e.adjustments_cents)}</td><td>${esc([e.claim_status, e.carc, e.adjustment_status, e.denial_category].filter(Boolean).join(' · '))}</td></tr>`).join('')}</tbody></table></div></details>`
      : '<div class="small muted" style="margin-top:8px">No EMA export rows for this claim.</div>'}`;
  const dd = claimShell(`Claim ${esc(c.bill)}`, `${esc(c.patient||'')}${c.member_id?' · member ID '+esc(c.member_id):''} · ${pl(c.remits.length, 'remittance')}${many?' · submissions '+c.claim_ids.map(esc).join(', '):''}`, body);
  const bk = dd.querySelector('#claimBack'); if(bk) bk.onclick = () => openDetail(backTo);
  dd.querySelectorAll('tr[data-fid]').forEach(tr=>{ const go = () => { state.worklist = null; state.wlNext = null; openDetail(tr.dataset.fid); };
    tr.onclick = go; tr.onkeydown = e => { if(e.key==='Enter'){ e.preventDefault(); go(); } }; });
  dd.querySelector('#claimCsv').onclick = () => {
    const cell = x => { const s = String(x == null ? '' : x); return /^[=+\-@\t\r]/.test(s) && isNaN(Number(s)) ? "'"+s : (/[",\n]/.test(s) ? '"'+s.replace(/"/g,'""')+'"' : s); };
    const head = ['claim_id','payment_date','payer','status','line','service_date','code','modifiers','billed_code','units','charge','allowed','paid','adjustments','remarks'];
    const out = [head.join(',')];
    c.remits.forEach(r => (r.lines||[]).forEach(l => out.push([r.patient_control_number, r.payment_date, r.payer_name, r.status_words, l.line_seq, l.service_date, l.procedure_code,
      (()=>{ try { return JSON.parse(l.modifiers||'[]').join(' '); } catch(e){ return ''; } })(), l.original_procedure_code, l.paid_units, (l.charge_cents||0)/100, (l.allowed_cents||0)/100, (l.paid_cents||0)/100,
      (l.adjustments||[]).map(a=>`${a.group_code}-${a.reason_code} ${(a.amount_cents||0)/100}`).join('; '), (l.remarks||[]).map(x=>x.code).join(' ')].map(cell).join(','))));
    const url = URL.createObjectURL(new Blob([out.join('\n')], {type:'text/csv'})), a = document.createElement('a');
    a.href = url; a.download = `remitwatch-claim-${c.bill}.csv`; document.body.appendChild(a); a.click(); a.remove(); setTimeout(()=>URL.revokeObjectURL(url), 10000); };
}
