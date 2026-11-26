/* RemitWatch: appeals by fax (remitwatch.fax). "Appeal by fax" for the items ticked on a list, "Appeal everything
   appealable", the Faxes page (the payers' numbers, what went out, what came in, the follow-ups due), the item's faxes
   in its panel, and the suggested templates on Bigger wins (remitwatch.lessons).
   Loaded after features.js and uses the helpers of index.html and features.js ($, api, post, esc, money, money2, state,
   render, openDetail, kindFor, pl). */

const FAX_KINDS = new Set(['note','rule','rate']);
// a stored moment's day where the practice is (today.js practiceDate), as the server says it; as written without it
const faxDay = s => typeof practiceDate === 'function' ? practiceDate(s) : String(s || '').slice(0, 10);
// a stored moment's date and time where the practice is (today.js practiceTime); UTC's, said so, without it
const faxTime = s => typeof practiceTime === 'function' ? practiceTime(s) : String(s || '').replace('T', ' ').slice(0, 16) + (/T\d\d:\d\d/.test(String(s || '')) ? ' UTC' : '');
const FAX_PURPOSE = {appeal:'Appeal', second_request:'Request again', next_level:'Next level', records:'Records sent', payment:'Payment request'};
const FAX_DOC = {appeal_decision:'Decision', records_request:'Records request', payment_notice:'Payment notice', denial:'Denial',
  acknowledgement:'Received notice', other:'Other', unread:'Not read'};
const FAX_DECISION = {overturned:['Overturned','ok'], partially_overturned:['Partly overturned','ok'], upheld:['Upheld','sev-high'], pending:['Pending','muted']};
// what each hold means, in the order a person fixes them; a hold with a code not listed here is still listed, under its
// own reason (faxSummary), so no item held at send time is left unnamed
const HOLDS = [
  ['bulk_paused', 'Sending in bulk is paused on this site: open each item and fax it from its panel'],
  ['practice', 'The practice\'s details are missing from the letters'], ['no_fax', 'The fax line is not set up'],
  ['paid_after', 'EMA shows a payment after the denial: open each one and check it before faxing'],
  ['no_balance', 'EMA shows no balance left on the line: open each one and check it before faxing'],
  ['stale', 'The remittances are behind: these may have been paid since'], ['check_code', 'Check in EMA which code went out first (open each one)'],
  ['left_out', 'Not appealed in bulk: its own next step is not an appeal now (open each one)'],
  ['blocked', 'Not appealed: paid, credited, reprocessed, closed or waiting on the payer'],
  ['check_first', 'Check first that it is still unpaid: EMA shows it settled, or the archive shows it likely paid'],
  ['no_remittance', 'A denial only EMA shows: wait for the payer\'s remittance or letter first'],
  ['packet_changed', 'The packet changed after it was read: build it again and read it'],
  ['no_number', 'No fax number saved for the payer'], ['by_hand', 'Fax by hand: the payer group holds many insurers, each with its own fax line'],
  ['no_fax_route', 'Not by fax: this payer files no faxed appeal at this level. File these on its portal or by mail'], ['no_ema', 'EMA is not connected'], ['ema_error', 'EMA did not answer'],
  ['ambiguous', 'The visit note needs a person to pick it or attach it: open each item'], ['no_note', 'No visit note yet (from EMA, or uploaded on the Upload page)'],
  ['note_check', 'A person checks the visit note first: open each item'], ['in_flight', 'A fax for it is going out now'], ['unknown_send', 'A fax for it may have gone out: look on the Faxes page'],
  ['review_no', 'Claude\'s review: the note does not support it (counted in Suggested templates)'], ['review_fix', 'Correct and resubmit instead'],
  ['review_caution', 'Claude\'s review has doubts: a person reads the packet first'], ['quotes', 'Claude quoted words the note does not have: a person checks the packet'],
  ['blanks', 'The letter still has blanks'], ['fax_error', 'The fax line refused it'], ['expired', 'Filing window closed'],
  ['not_appealable', 'Not something a letter settles'], ['no_claim', 'Not tied to one claim'], ['no_packet', 'Packet missing'], ['failed', 'Failed']];
// held as stale only because EMA could not vouch for the item (no report, an old one, a line not tied to its claim): a
// person who checked in EMA may fax these anyway. EMA's own sign of a payment (paid_after, no_balance) is never overridden in bulk
const FAX_ANYWAY = new Set(['no_ema', 'production_old', 'not_mapped']);
// an appeal sent again after every fax failed: its own Marked Sent is why it waits (or is due a check), not the payer
const SENT_BLOCKS = new Set(['waiting', 'follow_up', 'call', 'note']);
const FAX_SAYS = [['upheld','Upheld (denied again)'], ['overturned','Overturned (approved)'], ['partially_overturned','Partly overturned'],
  ['records_request','Asks for records'], ['other','Decides nothing']];

function faxModal(title, body){
  $('#modalBox').innerHTML = `<div style="display:flex;justify-content:space-between;gap:8px"><h2 style="margin:0">${esc(title)}</h2><button id="closeM" aria-label="Close">✕</button></div>${body}`;
  $('#modal').classList.add('open');
  $('#closeM').onclick = () => $('#modal').classList.remove('open');
  return $('#modalBox');
}
// whether this site faxes appeals in bulk (REMITWATCH_BULK_APPEAL, /api/meta bulk_appeal): while it is paused no page offers
// a bulk appeal button, only the one-item send in each item's panel
const bulkOn = () => !!(typeof state !== 'undefined' && state.meta && state.meta.bulk_appeal);
const faxBar = (done, n) => `<div class="bar" style="margin:6px 0"><i style="width:${n ? Math.round(100*done/n) : 0}%"></i></div>`;
// a US fax number as its ten digits, or '' (the server checks it the same way)
function faxDigits(n){ let d = String(n||'').replace(/\D/g,''); if(d.length === 11 && d[0] === '1') d = d.slice(1); return d.length === 10 && !'01'.includes(d[0]) ? d : ''; }

// Adding or changing a payer's fax number decides where patient records are faxed: where the site has an owner's password,
// the call carries it. It is asked for once and kept in memory like the Providers tab's (Lock forgets it).
function faxAskOwner(where, why){
  return new Promise(resolve => {
    where.className = 'small';
    where.innerHTML = `<div>${esc(why || 'Adding or changing a fax number needs the owner\'s password.')}</div>
      <input type="password" autocomplete="off" placeholder="Owner's password" style="width:180px"> <button data-ok>Use it</button> <button data-no>Cancel</button>`;
    const inp = where.querySelector('input'); inp.focus();
    where.querySelector('[data-ok]').onclick = () => resolve(inp.value);
    inp.onkeydown = e => { if(e.key === 'Enter') resolve(inp.value); };
    where.querySelector('[data-no]').onclick = () => { where.innerHTML = ''; resolve(''); };
  });
}
async function faxOwnerPost(path, body, where){
  const send = key => api(path, {method:'POST', headers: Object.assign({'Content-Type':'application/json'}, key ? {'X-Owner-Password': key} : {}), body: JSON.stringify(body||{})});
  try { return await send(state.ownerKey || ''); }
  catch(e){
    if(e.status !== 403 || !where) throw e;
    const key = await faxAskOwner(where, e.message);
    if(!key) throw new Error('Not saved.');
    const r = await send(key);
    state.ownerKey = key;
    if(typeof pvKeyUi === 'function') pvKeyUi();
    if(typeof pvArmIdle === 'function') pvArmIdle();
    return r;
  }
}
// the number read back, with the payer and what goes to it, before anything is saved or sent
function faxReadBack(payer, purpose, digits, extra){
  return confirm(`${digits ? `Save ${fax10(digits)} as the ${purpose === 'level_2' ? 'second-level' : 'appeal'} fax number of ${payer}?` : `Remove the ${purpose === 'level_2' ? 'second-level' : 'appeal'} fax number of ${payer}?`}`
    + (extra ? `\n\n${extra}` : '') + (digits ? '\n\nCheck each digit against the payer\'s own papers. A fax with a patient\'s record must not go to a wrong number.' : ''));
}

// ---------------------------------------------------------------- send: each item through /api/appeals/send, three at a time
async function appealByFax(ids, opts){
  opts = opts || {};
  ids = [...new Set(ids)];
  if(!ids.length) return;
  const box = faxModal(opts.title || 'Appealing by fax', `<div id="fxProg" class="small" aria-live="polite">Starting…</div><div id="fxBar">${faxBar(0, ids.length)}</div>
    <div class="note">For each item: the visit note from EMA where the appeal needs it, Claude's review of it (about a minute), the letter, and the fax to the payer. Keep this page open until it finishes.</div>
    <div id="fxOut"></div>`);
  const res = []; let next = 0, stopped = '';
  const tally = () => { const s = res.filter(r=>r.status==='sent'), prog = box.querySelector('#fxProg'), bar = box.querySelector('#fxBar');
    if(!prog || !bar) return;          // the window now shows something else
    prog.textContent = `${res.length} of ${ids.length} done · ${s.length} faxed (${money(s.reduce((a,r)=>a+(r.cents||0),0))}) · ${res.filter(r=>r.status==='held').length} held${stopped ? ' · stopped: '+stopped : ''}`;
    bar.innerHTML = faxBar(res.length, ids.length); };
  // closing the window stops the batch: what is on its way finishes, nothing new starts (the server never faxes one twice)
  const shown = () => $('#modal').classList.contains('open') && !!box.querySelector('#fxProg');
  async function worker(){
    while(next < ids.length && !stopped){
      if(!shown()){ stopped = 'the window was closed'; break; }
      const id = ids[next++];
      let r;
      try { r = await post('/api/appeals/send', {finding_id: id, stale_ok: !!opts.staleOk, bulk: true}); }
      catch(e){ r = {finding_id: id, status: 'held', code: e.code || 'failed', reason: e.message}; if([401,403,429].includes(e.status)) stopped = e.message; }
      res.push(r);
      // a hold that every item would hit: say it once instead of running through the rest. Stale is not one of them: EMA
      // vouches item by item (W5), so each item is tried
      if(r.code === 'practice' || r.code === 'no_fax' || r.code === 'bulk_paused') stopped = r.reason;
      tally();
    }
  }
  await Promise.all(Array.from({length: Math.min(3, ids.length)}, worker));
  tally();
  state.brief = null; state.dirty = true;
  if(!box.querySelector('#fxOut')) return;
  const paused = res.some(r => r.code === 'bulk_paused');
  faxSummary(box.querySelector('#fxOut'), res, ids.length - res.length, Object.assign({}, opts, {paused}));
  // "Fax them anyway" goes again only for the items EMA could not vouch for, never one EMA shows paid or closed
  const anyway = res.filter(r => r.code === 'stale' && FAX_ANYWAY.has(r.vouch || 'no_ema')).map(r => r.finding_id);
  if(anyway.length && !opts.staleOk){
    const out = box.querySelector('#fxOut'), b = document.createElement('div');
    b.className = 'actions';
    b.innerHTML = `<button id="fxStaleGo">${anyway.length === 1 ? 'Fax it anyway' : `Fax these ${anyway.length} anyway`}</button><span class="small muted">Only when you checked in EMA that ${anyway.length === 1 ? 'it is' : 'they are'} still unpaid. ${pl(anyway.length, 'item')}: ${esc(res.filter(r => anyway.includes(r.finding_id)).map(r => (r.payer_group || 'item') + ' ' + money2(r.cents)).slice(0, 8).join(', '))}${anyway.length > 8 ? '…' : ''}</span>`;
    out.prepend(b);
    b.querySelector('#fxStaleGo').onclick = () => appealByFax(anyway, Object.assign({}, opts, {staleOk: true}));
  }
}
function faxSummary(el, res, notTried, opts){
  const sent = res.filter(r=>r.status==='sent'), skipped = res.filter(r=>r.status==='skipped'), held = res.filter(r=>r.status==='held');
  const by = {}; held.forEach(r=>{ (by[r.code||'failed'] = by[r.code||'failed'] || []).push(r); });
  const item = r => `<li><a href="#" data-open="${esc(r.finding_id)}">${esc(r.payer_group||'item')} · ${money2(r.cents)}</a> <span class="small muted">${esc(r.reason||'')}</span></li>`;
  // every code held, the listed ones first in HOLDS' order, then any other under its own reason
  const known = new Set(HOLDS.map(h => h[0]));
  const groups = HOLDS.concat(Object.keys(by).filter(k => !known.has(k)).map(k => [k, 'Not sent'])).filter(([k])=>by[k]).map(([k, label]) => {
    const rs = by[k];
    if(k === 'no_number'){
      const payers = {}; rs.forEach(r=>{ (payers[r.payer_group||''] = payers[r.payer_group||''] || []).push(r); });
      return `<div class="panel"><b>${esc(label)}</b> <span class="small muted">${pl(rs.length, 'item')}</span>
        <div class="note">Type each payer's appeal fax number once, from its provider manual, its appeal form or a letter it sent. Faxes go only to numbers a person saved.</div>
        <table class="small"><tbody>${Object.entries(payers).map(([p, xs])=>`<tr><td>${esc(p)}</td><td class="num">${pl(xs.length,'item')} · ${money(xs.reduce((a,r)=>a+(r.cents||0),0))}</td>
          <td><input data-fxnum="${esc(p)}" placeholder="(800) 555-0100" inputmode="tel" style="width:150px"> <button data-fxsave="${esc(p)}">Save and send these</button> <span class="small" data-fxmsg="${esc(p)}"></span></td></tr>`).join('')}</tbody></table></div>`;
    }
    const one = rs[0].reason || '';
    const same = rs.every(r => r.reason === one);
    return `<div class="panel"><b>${esc(label)}</b> <span class="small muted">${pl(rs.length, 'item')}</span>${same && one ? `<div class="small">${esc(one)}</div>` : ''}
      <ul class="small">${rs.slice(0, 30).map(r => same ? `<li><a href="#" data-open="${esc(r.finding_id)}">${esc(r.payer_group||'item')} · ${money2(r.cents)}</a></li>` : item(r)).join('')}${rs.length > 30 ? `<li class="muted">and ${rs.length-30} more</li>` : ''}</ul></div>`;
  }).join('');
  el.innerHTML = `<div class="panel" style="margin-top:8px"><b class="ok">${pl(sent.length, 'appeal')} faxed</b>${sent.length ? ` · ${money(sent.reduce((a,r)=>a+(r.cents||0),0))}. Each is marked Sent; the Faxes page shows when the payer's line took it, and the follow-ups go out by themselves.` : '.'}
      ${skipped.length ? `<div class="small muted">${pl(skipped.length, 'item')} left alone: already sent, or no longer open.</div>` : ''}
      ${notTried ? `<div class="small sev-medium">${pl(notTried, 'item')} not tried${opts.paused ? ': sending in bulk is paused on this site; open each item and fax it from its panel' : ': fix what is below, then send again'}.</div>` : ''}</div>${groups}
    <div class="actions" style="margin-top:8px"><button id="fxDone" class="primary">Done</button><button id="fxFaxes">Open the Faxes page</button></div>`;
  el.querySelectorAll('[data-open]').forEach(a => a.onclick = e => { e.preventDefault(); $('#modal').classList.remove('open'); openDetail(a.dataset.open); });
  el.querySelectorAll('[data-fxsave]').forEach(b => b.onclick = async () => {
    const p = b.dataset.fxsave, input = el.querySelector(`[data-fxnum="${CSS.escape(p)}"]`), msg = el.querySelector(`[data-fxmsg="${CSS.escape(p)}"]`);
    const d = faxDigits(input.value), items = by.no_number.filter(r => (r.payer_group||'') === p);
    if(!d){ msg.textContent = 'Type the ten digits of the payer\'s fax number.'; msg.className = 'small sev-high'; return; }
    if(!faxReadBack(p, 'appeal', d, `Then ${pl(items.length, 'appeal')} ${items.length === 1 ? 'is' : 'are'} faxed to it.`)) return;
    try { await faxOwnerPost('/api/faxes/payer', {payer_group: p, number: d}, msg); }
    catch(e){ msg.textContent = e.message; msg.className = 'small sev-high'; return; }
    appealByFax(items.map(r => r.finding_id), opts);
  });
  el.querySelector('#fxDone').onclick = () => { $('#modal').classList.remove('open'); if(opts.after) opts.after(); else render(); };
  el.querySelector('#fxFaxes').onclick = () => { $('#modal').classList.remove('open'); state.tab = 'faxes'; location.hash = 'faxes'; render(); };
}

// ---------------------------------------------------------------- everything appealable, in one go
async function appealEverything(opts){
  opts = opts || {};
  const box = faxModal('Appeal everything appealable', '<div class="muted small">Counting…</div>');
  let a;
  try { a = await api('/api/appeals/appealable' + (opts.payer ? '?payer=' + encodeURIComponent(opts.payer) : '')); }
  catch(e){ box.querySelector('.muted').textContent = 'Could not count them: ' + e.message; return; }
  if(a.paused){ box.querySelector('.muted').textContent = a.paused; return; }
  const ready = a.items.filter(i => i.ready), all$ = a.items.reduce((s,i)=>s+i.cents,0), ready$ = ready.reduce((s,i)=>s+i.cents,0);
  const needNote = a.items.filter(i => i.kind !== 'rate').length;
  const warn = [];
  if(!a.fax) warn.push(a.not_here || 'The fax line is not set up yet (SRFax: SRFAX_ACCESS_ID, SRFAX_ACCESS_PWD, SRFAX_CALLER_ID in the host\'s settings). Nothing can go until it is.');
  if((a.practice_missing||[]).length) warn.push('The letters still lack the practice\'s details: set ' + a.practice_missing.join(', ') + '.');
  const byHand = typeof state !== 'undefined' && state.meta && state.meta.uploads;      // a server takes the notes by hand
  if(needNote && !a.modmed) warn.push(byHand
    ? `${pl(needNote, 'item')} need${needNote===1?'s':''} the visit note: the ones whose note was uploaded from EMA (the Upload page) go; the others are held until it is. The rate ones still go.`
    : `${pl(needNote, 'item')} need${needNote===1?'s':''} the visit note and EMA is not connected: those are held. The rate ones still go.`);
  if(needNote && !a.review) warn.push('Claude\'s review is off (ANTHROPIC_API_KEY): letters that rest on the note keep their blanks and are held.');
  // what the list leaves out and why (paid, waiting, a credit, reprocessed): one click lists them, each opening its own panel
  const lo = a.left_out || {};
  const body = `<div class="small" style="margin:6px 0"><b>${pl(a.items.length, 'item')}</b> · ${money(all$)}: every open item to work now that a letter settles (the visit note, the coding rules, or the payer's own rate), inside its filing window and not faxed yet. Each is checked again against its own next step when it is sent.</div>
    ${lo.n ? `<div class="small" style="margin:4px 0"><a href="#" id="fxLeft">${esc(lo.line)}</a><span class="muted">: never appealed in bulk.</span></div><div id="fxLeftList"></div>` : ''}
    ${warn.map(w=>`<div class="small sev-medium">${esc(w)}</div>`).join('')}
    <table class="small" style="margin-top:8px"><thead><tr><th>Payer</th><th class="num">Items</th><th>Appeal fax number</th></tr></thead><tbody>
    ${a.payers.map(p=>`<tr><td>${esc(p.payer_group||'(no payer)')}</td><td class="num">${num(p.n)} · ${money(p.cents)}</td>
      <td>${p.by_hand ? `<span class="small muted">${esc(p.by_hand)}</span>` : p.number ? `${esc(p.number)}` : `<input data-fxnum="${esc(p.payer_group)}" placeholder="(800) 555-0100" inputmode="tel" style="width:150px"> <button data-fxsave="${esc(p.payer_group)}">Save</button> <span class="small" data-fxmsg="${esc(p.payer_group)}"></span>`}</td></tr>`).join('')}</tbody></table>
    <div class="note">A number is typed once, from the payer's provider manual, appeal form or one of its letters, and checked by the person who types it: a fax with a patient's record must not go to a wrong number.</div>
    <div class="actions"><button class="primary" id="fxGo" ${ready.length && a.fax ? '' : 'disabled'}>Send the ${num(ready.length)} with a number (${money(ready$)})</button>
      <button id="fxCancel">Not now</button></div>`;
  box.innerHTML = box.innerHTML.replace('<div class="muted small">Counting…</div>', body);
  $('#closeM').onclick = () => $('#modal').classList.remove('open');
  box.querySelectorAll('[data-fxsave]').forEach(b => b.onclick = async () => {
    const p = b.dataset.fxsave, input = box.querySelector(`[data-fxnum="${CSS.escape(p)}"]`), msg = box.querySelector(`[data-fxmsg="${CSS.escape(p)}"]`);
    const d = faxDigits(input.value);
    if(!d){ msg.textContent = 'Type the ten digits of the payer\'s fax number.'; msg.className = 'small sev-high'; return; }
    if(!faxReadBack(p, 'appeal', d)) return;
    try { await faxOwnerPost('/api/faxes/payer', {payer_group: p, number: d}, msg); appealEverything(opts); }
    catch(e){ msg.textContent = e.message; msg.className = 'small sev-high'; }
  });
  box.querySelector('#fxCancel').onclick = () => $('#modal').classList.remove('open');
  const lf = box.querySelector('#fxLeft');
  if(lf) lf.onclick = e => { e.preventDefault(); const el = box.querySelector('#fxLeftList'); if(el.innerHTML){ el.innerHTML = ''; return; }
    el.innerHTML = `<table class="small"><tbody>${(lo.items||[]).map(x=>`<tr class="row" tabindex="0" data-left="${esc(x.finding_id)}"><td>${esc(x.payer_group||'')}</td><td>${esc(x.words||x.reason)}</td><td class="num">${money2(x.cents)}</td></tr>`).join('')}</tbody></table>${lo.n > (lo.items||[]).length ? `<div class="small muted">The first ${num((lo.items||[]).length)} of ${num(lo.n)}.</div>` : ''}`;
    el.querySelectorAll('[data-left]').forEach(tr => tr.onclick = () => { $('#modal').classList.remove('open'); openDetail(tr.dataset.left); }); };
  box.querySelector('#fxGo').onclick = () => {
    if(ready.length > 20 && !confirm(`Fax ${pl(ready.length, 'appeal')} now? Each takes about a minute; keep this page open.`)) return;
    appealByFax(ready.map(i => i.finding_id), {title: 'Appealing everything appealable', after: opts.after});
  };
}

// ---------------------------------------------------------------- the item's panel
function faxDrawerHtml(f, r){
  const kind = state.brief ? kindFor(state.brief, f) : '';
  const list = r.faxes || [], line = !!(state.meta && state.meta.fax);
  const outs = list.filter(x => x.direction !== 'in');
  // an appeal whose every try failed (a busy line, a number replaced since) can be sent again, to the number saved now
  const again = FAX_KINDS.has(kind) && f.status === 'appealed' && !!f.claim_key && outs.length > 0 && outs.every(x => x.status === 'failed');
  const can = (FAX_KINDS.has(kind) && ['open','in_progress'].includes(f.status) && !!f.claim_key) || again;
  if(!can && !list.length && !(line && f.claim_key)) return '';
  const row = x => `<tr><td>${esc(faxTime(x.created_at))}</td><td>${x.direction==='in' ? 'From the payer' : 'To ' + esc(fax10(x.number))}</td>
    <td>${x.direction==='in' ? esc(FAX_DOC[x.purpose]||x.purpose||'') + (x.decision && FAX_DECISION[x.decision] ? ` · <span class="${FAX_DECISION[x.decision][1]}">${FAX_DECISION[x.decision][0]}</span>` : '')
      : esc(FAX_PURPOSE[x.purpose]||x.purpose||'') + (x.level > 1 ? ' (level ' + esc(x.level) + ')' : '')}</td>
    <td>${x.direction==='in' ? '' : `<span class="${x.status==='failed'?'sev-high':x.status==='sent'?'ok':'muted'}">${esc(x.status||'')}</span>`}</td>
    <td class="small muted">${esc(x.summary||x.error||'')}</td><td><a href="/api/faxes/pdf?key=${encodeURIComponent(x.fax_key)}" target="_blank" rel="noopener">PDF</a></td></tr>`;
  // an item whose own next step says not to appeal (paid, settled, credited, reprocessed or waiting) offers no fax; an
  // appeal sent again after every fax failed waits only because of its own Marked Sent, so that block alone does not stop it
  const blocked = !!f.appeal_block && !(again && SENT_BLOCKS.has(f.appeal_block));
  return `<div class="fax-box" style="margin:8px 0">${can && !blocked ? `<div class="actions" id="fxOneRow"><button id="fxOne">${again ? 'Send the appeal again' : 'Appeal by fax'}</button><span class="small muted">${again
      ? 'Its faxes never got through. It goes to the number saved for the payer now.' : 'You read the packet first; it goes to the payer\'s confirmed number and the item is marked Sent.'}</span></div>` : ''}
    ${list.length ? `<h3 class="small">Faxes</h3><table class="small"><tbody>${list.map(row).join('')}</tbody></table>` : ''}
    ${line && f.claim_key ? '<div><button class="ghost small" id="fxTie">Tie a fax that came in to this item</button></div>' : ''}
    <div id="fxOneMsg" class="small"></div></div>`;
}
function fax10(n){ const d = faxDigits(n); return d ? `(${d.slice(0,3)}) ${d.slice(3,6)}-${d.slice(6)}` : String(n||''); }
function bindFaxDrawer(id, d, seq, what, r){
  const b = d.querySelector('#fxOne'); if(!b) return;
  // with the item at hand, what goes is read first (features.js xpFaxPreview): the packet, or for a rate request its letter
  if(r && r.finding && typeof xpFaxPreview === 'function'){ b.onclick = () => xpFaxPreview(r.finding, r.packets, d); return; }
  // what the person checked in EMA, each answer kept apart: "still unpaid" says nothing about which code went out
  const said = {};
  // the panel still shows this item: else what comes back is said in a notice, and the panel the person is on is left alone
  const here = () => typeof panelShows !== 'function' || panelShows(seq, id);
  const msg = d.querySelector('#fxOneMsg');           // taken now: an answer never writes into another item's panel
  let busy = false;
  const go = async () => {
    if(busy) return;                                  // one send at a time from this panel: a second click does nothing
    busy = true; b.disabled = true; msg.querySelectorAll('button').forEach(x => x.disabled = true);
    msg.className = 'small muted'; msg.textContent = 'Reading the note, writing the letter and faxing it. About a minute.';
    let r;
    try { r = await post('/api/appeals/send', Object.assign({finding_id: id}, said)); }
    catch(e){ busy = false;
      if(!here()){ if(typeof notice === 'function') notice(`Appeal by fax (${what || 'an item'}): ${e.message}`); return; }
      msg.className = e.code === 'in_flight' ? 'small sev-medium' : 'small sev-high'; msg.textContent = e.message; b.disabled = false; return; }
    busy = false;
    if(r.status === 'sent'){ state.brief = null; state.dirty = true;
      // kept: the item changed while the fax went out (the run's Paid, a hide), so it was not marked Sent; its history says so
      if(here()) openDetail(id); else if(typeof notice === 'function') notice(`Faxed: ${what || 'an item'}${r.number ? ' to ' + r.number : ''}. ${r.kept || 'It is marked Sent.'}`);
      return; }
    if(!here()){ if(typeof notice === 'function') notice(`Not faxed (${what || 'an item'}): ${r.reason || 'Not sent.'} Open it again to finish.`); return; }
    msg.className = 'small sev-medium';
    msg.innerHTML = esc(r.reason || 'Not sent.') + (r.code === 'no_number' ? ' <a href="#faxes" id="fxGoNums">Open the Faxes page</a>' : '')
      + ((r.candidates||[]).length ? ' Open "Build the appeal from the visit note" below and pick it there.' : '');
    if(r.code === 'check_code') msg.innerHTML += ' <button id="fxCode">It went out as billed: fax it</button>';
    if(r.code === 'stale') msg.innerHTML += ' <button id="fxStale">Still unpaid in EMA: fax it anyway</button>';
    if(r.code === 'note_check' && r.packet_id) msg.innerHTML += ` <a href="/api/packets/${encodeURIComponent(r.packet_id)}/pdf" target="_blank" rel="noopener">Open the packet</a>`;
    if(r.code === 'note_check' && r.confirm) msg.innerHTML += `<div style="margin-top:4px"><label><input type="checkbox" id="fxNoteOk"> I opened this note: it is this patient's visit on ${esc(r.visit_date ? longDate(r.visit_date) : 'the date of service')}</label> <button id="fxNoteGo" disabled>Fax it</button></div>`;
    if(r.code === 'check_first') msg.innerHTML += ' <button id="fxChecked">Checked: still unpaid, fax it</button>';
    // the buttons the answer brings are looked for in this item's own message, and act for this item only
    const g = msg.querySelector('#fxGoNums'); if(g) g.onclick = e => { e.preventDefault(); closeDetail(); state.tab = 'faxes'; location.hash = 'faxes'; render(); };
    const code = msg.querySelector('#fxCode'); if(code) code.onclick = () => { if(code.disabled) return; said.confirmed = true; go(); };
    const stale = msg.querySelector('#fxStale'); if(stale) stale.onclick = () => { if(stale.disabled) return; said.stale_ok = true; go(); };
    const checked = msg.querySelector('#fxChecked'); if(checked) checked.onclick = () => { if(checked.disabled) return; said.checked = true; go(); };
    const ok = msg.querySelector('#fxNoteOk'), send = msg.querySelector('#fxNoteGo');
    if(ok && send){ ok.onchange = () => { send.disabled = !ok.checked; };
      send.onclick = () => { if(send.disabled || !ok.checked) return; said.note_checked = true; said.packet_id = r.packet_id; go(); }; }
    b.disabled = false;
  };
  b.onclick = () => go();
}

// ---------------------------------------------------------------- the Faxes page
const FAX_RESEND = new Set(['second_request','next_level','records','payment']);
async function faxesTab(v){
  const [fx, pp] = await Promise.all([api('/api/faxes'), api('/api/faxes/payers')]);
  const due = fx.due || [], pushes = due.filter(x => x.action !== 'person' && !x.hold), people = due.filter(x => x.action === 'person' || x.hold);
  const unmatched = (fx.received||[]).filter(x => !x.finding_id);
  const sends = fx.sends !== false;
  const numInput = (p, purpose) => { const n = (p.numbers||{})[purpose] || {}, cur = n.number || '';
    const when = s => esc(faxTime(s));
    return `<input data-pfax="${esc(p.payer_group)}" data-purpose="${purpose}" value="${esc(cur)}" placeholder="${purpose==='appeal'?'(800) 555-0100':'if different'}" inputmode="tel" style="width:140px"><button data-psave="${esc(p.payer_group)}" data-purpose="${purpose}">Save</button>`
      + (cur ? `<div class="muted" title="${esc((n.history||[]).map(h => `${h.number} by ${h.by||'?'} on ${faxDay(h.at)}${h.confirmed ? ' (confirmed)' : ''}`).join('\n'))}">Saved by ${esc(n.by||'?')} on ${esc(faxDay(n.at))}</div>` : '')
      + (cur && n.confirmed === false ? `<div class="sev-medium">Changed${n.changed_at ? ' on ' + esc(faxDay(n.changed_at)) : ''}${n.previous ? ` (was ${esc(n.previous)})` : ''}. Retries and follow-ups wait until someone confirms it.
          <button data-pconfirm="${esc(p.payer_group)}" data-purpose="${purpose}" data-number="${esc(cur)}">Confirm ${esc(cur)}</button></div>` : ''); };
  const nth = k => { const r = [11,12,13].includes(k % 100) ? 0 : k % 10; return k + (r === 1 ? 'st' : r === 2 ? 'nd' : r === 3 ? 'rd' : 'th'); };
  const what = x => x.action === 'second_request' ? `Ask again (${nth(x.n + 1)} request)`
    : ({next_level:'Ask for the next level', records:'Send the records', payment:'Ask for the payment', person:'A person decides'}[x.action] || x.action);
  // what a person can do about one item, in one click: send it to the number saved now, or say whether a send went out
  const personDo = x => {
    if(x.settle) return `<button data-settle="${esc(x.settle)}" data-went="1">It went out</button> <button data-settle="${esc(x.settle)}" data-went="0">It did not go out</button>`;
    const act = x.action === 'person' ? x.resend : x.action;
    // a request that says the claim is unpaid while the remittances are behind: sent only after a person checked EMA
    if(sends && x.number && FAX_RESEND.has(act)) return `<button data-send="${esc(x.finding_id)}" data-number="${esc(x.number)}"${x.stale ? ' data-stale="1"' : ''}>${x.stale ? 'Still unpaid in EMA: send it to ' : 'Send it to '}${esc(x.number)}</button>`;
    if(x.resend === 'appeal') return '<span class="muted">Open the item: Send the appeal again</span>';
    return '';
  };
  const says = x => `<select data-says="${esc(x.fax_key)}"><option value="">What it says (when it was not read)</option>${FAX_SAYS.map(([k,l])=>`<option value="${k}">${esc(l)}</option>`).join('')}</select>`;
  const unread = x => !x.document_type || x.purpose === 'unread' || !!x.error;
  const itemCell = x => x.finding_id
    ? `<a href="#" data-open="${esc(x.finding_id)}">${esc(x.title||'Open the item')}</a>${(x.tied||[]).length > 1 ? `<div class="muted">and ${pl(x.tied.length - 1, 'other item')} of the claim</div>` : ''}`
    : `<span class="sev-medium">Needs a person to match</span>${x.needs ? `<div class="muted">${esc(x.needs)}</div>` : ''}
       ${(x.suggested||[]).length ? `<div>${unread(x) ? says(x) : ''}${x.suggested.map(s => `<div><button data-tie="${esc(x.fax_key)}" data-to="${esc(s.finding_id)}">Tie to: ${esc(s.title||'item')} · ${esc(s.service_date||'')} · ${esc(s.payer_group||'')}${s.patient_control_number ? ' · claim ' + esc(s.patient_control_number) : ''}</button></div>`).join('')}</div>` : ''}`;
  v.innerHTML = `<div class="page-head ph-row"><div><h1 class="page-title">Faxes</h1> <span class="muted">Appeals faxed to payers, their answers read and matched, and the follow-ups that keep going every week until each claim is paid or decided.</span></div>
      <div class="ph-go">${fx.configured && sends ? '<button id="fxCheck">Check for faxes now</button>' : ''}${bulkOn() ? '<button class="primary" id="fxAll">Appeal everything appealable</button>' : ''}</div></div>
    <div class="panel"><b>The fax line:</b> ${fx.configured ? `${fx.line ? esc(fx.line) : 'set up'}${fx.line ? ' · payers answer to this number, so their letters come back here' : ' · set SRFAX_CALLER_ID to the line\'s number so letters give it'}` : '<span class="sev-medium">not set up yet</span>: sign up with SRFax (a healthcare plan, with its BAA), then set SRFAX_ACCESS_ID, SRFAX_ACCESS_PWD, SRFAX_CALLER_ID and SRFAX_SENDER_EMAIL in the host\'s settings. /setup checks it.'}
      ${fx.not_here ? `<div class="small sev-medium">${esc(fx.not_here)}</div>` : ''}
      ${(fx.practice_missing||[]).length ? `<div class="small sev-medium">The letters still lack the practice's details: set ${esc(fx.practice_missing.join(', '))}.</div>` : ''}
      <div id="fxCheckMsg" class="small muted"></div></div>
    <div class="panel"><h2>Follow-ups due <span class="muted small">${num(pushes.length)}</span></h2>
      <div class="note">An appeal with no decision after the payer's time to answer (60 days for Medicare and Medicare Advantage, 30 for commercial plans) gets a second request, then another every week; an upheld appeal goes to the next level with the payer's decision attached; approved but unpaid after 30 days gets a payment request. The daily run sends these by itself; this button sends them now. A follow-up waits for a person when the payer's number changed since the item's last fax.</div>
      ${pushes.length ? `<table class="small"><thead><tr><th>Item</th><th>Payer</th><th>Next</th><th>Why</th><th class="num">Amount</th></tr></thead><tbody>${pushes.map(x=>`<tr class="row" data-id="${esc(x.finding_id)}"><td>${esc(x.title||'')}<div class="muted">${esc(x.service_date||'')}</div></td><td>${esc(x.payer_group||'')}</td><td><b>${esc(what(x))}</b></td><td>${esc(x.why)}</td><td class="num">${money2(x.shortfall_cents)}</td></tr>`).join('')}</tbody></table>
        ${sends ? `<div class="actions" style="margin-top:6px"><button class="primary" id="fxPush">${pushes.length === 1 ? 'Send the follow-up now' : `Send the ${num(pushes.length)} follow-ups now`}</button><span id="fxPushMsg" class="small muted"></span></div>` : ''}` : '<div class="muted small">Nothing due. Each appeal sent is checked every day.</div>'}
      ${people.length ? `<h3 class="small" style="margin-top:10px">For a person <span class="muted">${num(people.length)}</span></h3><table class="small"><tbody>${people.map(x=>`<tr class="row" data-id="${esc(x.finding_id)}"><td>${esc(x.title||'')}</td><td>${esc(x.payer_group||'')}</td><td>${esc(x.hold || x.why)}</td><td class="num">${money2(x.shortfall_cents)}</td><td>${personDo(x)}</td></tr>`).join('')}</tbody></table><div id="fxPersonMsg" class="small"></div>` : ''}</div>
    <div class="panel"><h2>Payer fax numbers</h2><div class="note">Typed once per payer from its provider manual, appeal form or one of its letters, and read back before it is saved. Faxes go only to a number saved here.${pp.owner ? ' Adding or changing a number needs the owner\'s password.' : ''} A number that replaces another is used by retries and follow-ups only once someone confirms it. The phone and website are what the payer's remittances give, to look the number up.</div>
      <div class="scroll"><table class="small"><thead><tr><th>Payer</th><th class="num">Open appeals</th><th>Appeal fax</th><th>Second level fax</th><th>Look it up</th></tr></thead><tbody>
      ${(pp.payers||[]).map(p=>`<tr><td>${esc(p.payer_group)}<div class="muted">${esc(p.payer_kind||'')}</div></td><td class="num">${num(p.n)} · ${money(p.cents)}</td>${p.by_hand ? `<td colspan="2" class="muted">${esc(p.by_hand)}</td>` : `<td>${p.no_fax ? `<span class="small muted">${esc(p.no_fax)}</span>${(p.numbers||{}).appeal ? `<div class="small muted">A number saved before is never used:</div>${numInput(p,'appeal')}` : ''}` : numInput(p,'appeal')}</td><td>${numInput(p,'level_2')}</td>`}<td class="muted">${esc(fax10(p.phone))}${p.url?`<div>${esc(p.url)}</div>`:''}</td></tr>`).join('') || '<tr><td colspan="5" class="muted">No payer has an open appeal.</td></tr>'}
      </tbody></table></div><div id="pfMsg" class="small"></div></div>
    <div class="panel"><h2>Came in <span class="muted small">${num((fx.received||[]).length)}</span></h2>
      ${fx.review ? '' : '<div class="note sev-medium">Claude\'s reading of incoming faxes is off (ANTHROPIC_API_KEY): they are listed, not read or matched.</div>'}
      ${unmatched.length ? `<div class="note">${pl(unmatched.length, 'fax')} need a person to match. A fax is tied by itself only when a claim number on it, or the member ID with the patient's name and the date of service, names the item and it came from a number known for that payer. A member ID a family shares ties nothing by itself. Open the PDF, then tie it to the item it answers (or open the item and use "Tie a fax that came in").</div>` : ''}
      ${(fx.received||[]).length ? `<div class="scroll"><table class="small"><thead><tr><th>Received</th><th>From</th><th>What</th><th>Says</th><th>Item</th><th></th></tr></thead><tbody>${fx.received.map(x=>`<tr><td>${esc(faxTime(x.created_at))}</td><td>${esc(fax10(x.number))}${x.payer?`<div class="muted">${esc(x.payer)}</div>`:''}</td>
        <td>${esc(FAX_DOC[x.purpose]||x.purpose||'')}${x.decision && FAX_DECISION[x.decision] ? `<div class="${FAX_DECISION[x.decision][1]}">${FAX_DECISION[x.decision][0]}</div>` : ''}</td>
        <td>${esc(x.summary||x.error||'')}${x.deadline?`<div class="sev-medium">Deadline ${esc(x.deadline)}</div>`:''}${x.patient?`<div class="muted">${esc(x.patient)}</div>`:''}</td>
        <td>${itemCell(x)}</td>
        <td><a href="/api/faxes/pdf?key=${encodeURIComponent(x.fax_key)}" target="_blank" rel="noopener">PDF</a></td></tr>`).join('')}</tbody></table></div><div id="fxTieMsg" class="small"></div>` : '<div class="muted small">Nothing yet.</div>'}</div>
    <div class="panel"><h2>Sent <span class="muted small">${num((fx.sent||[]).length)}</span></h2>
      ${(fx.sent||[]).length ? `<div class="scroll"><table class="small"><thead><tr><th>Sent</th><th>To</th><th>What</th><th>State</th><th class="num">Pages</th><th>Item</th><th></th></tr></thead><tbody>${fx.sent.map(x=>`<tr><td>${esc(faxTime(x.created_at))}</td><td>${esc(x.payer_group||'')}<div class="muted">${esc(x.number||'')}</div></td>
        <td>${esc(FAX_PURPOSE[x.purpose]||x.purpose||'')}${x.level>1?` <span class="muted">(level ${esc(x.level)})</span>`:''}</td>
        <td><span class="${x.status==='failed'?'sev-high':x.status==='sent'?'ok':'muted'}">${esc(x.status==='queued'?'going out':x.status==='sending'?'not settled':x.status||'')}</span>${x.error?`<div class="muted">${esc(x.error)}</div>`:''}
          ${x.unsettled ? `<div class="small">Look in SRFax's sent faxes: <button data-settle="${esc(x.fax_key)}" data-went="1">It went out</button> <button data-settle="${esc(x.fax_key)}" data-went="0">It did not go out</button></div>` : ''}</td>
        <td class="num">${esc(x.pages||'')}</td><td>${x.finding_id ? `<a href="#" data-open="${esc(x.finding_id)}">${esc(x.title||'Open')}</a>` : ''}</td>
        <td>${x.packet_id ? `<a href="/api/faxes/pdf?key=${encodeURIComponent(x.fax_key)}" target="_blank" rel="noopener">Packet</a>` : ''}</td></tr>`).join('')}</tbody></table></div>` : `<div class="muted small">${bulkOn() ? 'Nothing sent yet. Tick items on a list and use Appeal by fax, or Appeal everything appealable above.' : 'Nothing sent yet. Open an item and use Appeal by fax in its panel.'}</div>`}</div>`;
  v.querySelectorAll('[data-open]').forEach(a => a.onclick = e => { e.preventDefault(); openDetail(a.dataset.open); });
  v.querySelectorAll('tr.row[data-id]').forEach(tr => tr.onclick = e => { if(e.target.closest('button,select,input,a')) return; openDetail(tr.dataset.id); });
  const all = v.querySelector('#fxAll'); if(all) all.onclick = () => appealEverything({after: render});
  const ck = v.querySelector('#fxCheck');
  if(ck) ck.onclick = async () => {
    const msg = v.querySelector('#fxCheckMsg'); ck.disabled = true;
    let total = {received: 0, matched: 0, sent: 0, failed: 0, retried: 0};
    for(let round = 0; round < 6; round++){
      msg.textContent = round ? 'Still reading new faxes…' : 'Checking the line and reading new faxes…';
      let r; try { r = await post('/api/faxes/refresh', {}); } catch(e){ msg.textContent = 'Could not check: ' + e.message; ck.disabled = false; return; }
      Object.keys(total).forEach(k => total[k] += r[k] || 0);
      if((r.errors||[]).length) msg.textContent = r.errors[0];
      if(!r.more) break;
    }
    render();
  };
  const push = v.querySelector('#fxPush');
  if(push) push.onclick = async () => {
    const msg = v.querySelector('#fxPushMsg'); push.disabled = true; msg.textContent = 'Sending…';
    try { const r = await post('/api/faxes/follow_up', {}); msg.textContent = `${pl(r.sent, 'follow-up')} faxed${r.held ? `, ${r.held} held` : ''}${r.more ? '; more next time' : ''}.`;
      const why = (r.results||[]).filter(x=>x.status==='held').map(x=>x.reason); if(why.length) msg.textContent += ' ' + why[0];
      setTimeout(render, 1200); }
    catch(e){ msg.textContent = e.message; push.disabled = false; }
  };
  // one item, by a person, to the number in front of them
  v.querySelectorAll('[data-send]').forEach(b => b.onclick = async () => {
    const msg = v.querySelector('#fxPersonMsg');
    const stale = b.dataset.stale === '1';
    if(!confirm(stale ? `You checked EMA or the payer's portal and this claim is still unpaid? Fax its follow-up to ${b.dataset.number}.`
                      : `Fax this item's follow-up to ${b.dataset.number}?`)) return;
    b.disabled = true;
    try { const r = await post('/api/faxes/follow_up', Object.assign({finding_ids: [b.dataset.send], number: b.dataset.number}, stale ? {stale_ok: true} : {}));
      const one = (r.results||[])[0] || {};
      msg.className = one.status === 'sent' ? 'small ok' : 'small sev-medium';
      msg.textContent = one.status === 'sent' ? `Faxed to ${one.number}.` : (one.reason || 'Not sent.');
      if(one.status === 'sent') setTimeout(render, 1200); else b.disabled = false; }
    catch(e){ msg.className = 'small sev-high'; msg.textContent = e.message; b.disabled = false; }
  });
  v.querySelectorAll('[data-settle]').forEach(b => b.onclick = async () => {
    const went = b.dataset.went === '1';
    if(!confirm(went ? 'You found this fax in SRFax\'s sent faxes: it went out?' : 'This fax is not in SRFax\'s sent faxes: it did not go out? The line then tries it again.')) return;
    try { await post('/api/faxes/settle', {fax_key: b.dataset.settle, went}); render(); }
    catch(e){ alert(e.message); }
  });
  v.querySelectorAll('[data-tie]').forEach(b => b.onclick = async () => {
    const msg = v.querySelector('#fxTieMsg'), sel = v.querySelector(`[data-says="${CSS.escape(b.dataset.tie)}"]`);
    if(!confirm('Tie this fax to that item? From then on it counts as the payer\'s answer about it.')) return;
    try { await post('/api/faxes/match', Object.assign({fax_key: b.dataset.tie, finding_id: b.dataset.to}, sel && sel.value ? {says: sel.value} : {})); render(); }
    catch(e){ msg.className = 'small sev-high'; msg.textContent = e.message; }
  });
  v.querySelectorAll('[data-psave]').forEach(b => b.onclick = async () => {
    const p = b.dataset.psave, purpose = b.dataset.purpose, input = [...v.querySelectorAll('[data-pfax]')].find(x => x.dataset.pfax === p && x.dataset.purpose === purpose);
    const msg = v.querySelector('#pfMsg'), d = faxDigits(input.value);
    if(input.value.trim() && !d){ msg.className = 'small sev-high'; msg.textContent = `${p}: type the ten digits of the fax number.`; return; }
    if(!faxReadBack(p, purpose, d)) return;
    try { const r = await faxOwnerPost('/api/faxes/payer', {payer_group: p, purpose, number: d}, msg);
      msg.className = 'small ok'; msg.textContent = r.number ? `${p}: ${purpose === 'appeal' ? 'appeal' : 'second level'} fax saved as ${r.number}.` : `${p}: removed.`;
      if(r.needs_confirm) msg.textContent += ` It replaced ${r.previous}: retries and follow-ups wait until someone confirms it.`;
      setTimeout(render, 1500); }
    catch(e){ msg.className = 'small sev-high'; msg.textContent = `${p}: ${e.message}`; }
  });
  v.querySelectorAll('[data-pconfirm]').forEach(b => b.onclick = async () => {
    const p = b.dataset.pconfirm, purpose = b.dataset.purpose, msg = v.querySelector('#pfMsg');
    if(!confirm(`Retries and follow-ups for ${p} go to ${b.dataset.number} from now on, each with the patient's records. Is that the payer's number, from its own papers?`)) return;
    try { await faxOwnerPost('/api/faxes/payer/confirm', {payer_group: p, purpose, number: b.dataset.number}, msg); render(); }
    catch(e){ msg.className = 'small sev-high'; msg.textContent = `${p}: ${e.message}`; }
  });
}

// ---------------------------------------------------------------- tie a fax that came in to the item open in the panel
async function tieFaxHtml(d, id, seq){
  const el = d.querySelector('#fxTie'); if(!el) return;
  el.onclick = async () => {
    const box = d.querySelector('#fxOneMsg');
    let fx; try { fx = await api('/api/faxes'); } catch(e){ box.textContent = e.message; return; }
    // the faxes that name this item first, then the others not tied yet, then those tied elsewhere (a wrong tie is undone here)
    const all = (fx.received||[]).filter(x => x.finding_id !== id && !(x.tied||[]).includes(id));
    const named = x => (x.suggested||[]).some(s => s.finding_id === id);
    const order = [...all.filter(named), ...all.filter(x => !named(x) && !x.finding_id), ...all.filter(x => !named(x) && x.finding_id)];
    if(!order.length){ box.className = 'small muted'; box.textContent = 'No fax that came in is left to tie here.'; return; }
    box.className = 'small';
    box.innerHTML = `<select id="fxTieSel">${order.map(x=>`<option value="${esc(x.fax_key)}">${named(x) ? 'Names this item · ' : x.finding_id ? 'Tied to another item · ' : ''}${esc(faxDay(x.created_at))} · ${esc(fax10(x.number))} · ${esc(FAX_DOC[x.purpose]||x.purpose||'')} · ${esc((x.summary||x.error||'').slice(0,80))}${(x.claim_numbers||[]).length ? ' · claim ' + esc(x.claim_numbers.join(', ')) : ''}${x.patient ? ' · ' + esc(x.patient) : ''}</option>`).join('')}</select>
      <select id="fxTieSays"><option value="">What it says (when it was not read)</option>${FAX_SAYS.map(([k,l])=>`<option value="${k}">${esc(l)}</option>`).join('')}</select> <button id="fxTieGo">Tie to this item</button>`;
    box.querySelector('#fxTieGo').onclick = async () => {
      const says = box.querySelector('#fxTieSays').value;
      try { await post('/api/faxes/match', Object.assign({fax_key: box.querySelector('#fxTieSel').value, finding_id: id}, says ? {says} : {}));
        if(typeof panelShows !== 'function' || panelShows(seq, id)) openDetail(id); else if(typeof notice === 'function') notice('The fax was tied to the item.'); }
      catch(e){ box.textContent = e.message; }
    };
  };
}

// ---------------------------------------------------------------- Bigger wins: suggested templates for next time
async function lessonsPanel(el){
  if(!el) return;
  let L; try { L = await api('/api/lessons'); } catch(e){ el.innerHTML = ''; return; }
  const codes = L.codes || [];
  el.innerHTML = `<h2>Suggested templates for next time</h2>
    <div class="small"><b>Next step:</b> have whoever builds EMA's templates make these fields required for the codes at the top, so the note carries what the payer asks before it is signed.</div>
    <details class="why-fold"><summary>Why</summary><div class="note">Each time Claude reviewed a visit note for an appeal and found it short (the note did not support the code billed, or only in part), what was missing is kept here, by code. Those claims could not be won on appeal; the template stops the next ones. The fields ask the clinician what was done: they never write the note for them, and the clinician signs that the note records their own work.</div></details>
    ${codes.length ? `<div class="small" style="margin:6px 0">${pl(L.n, 'note')} fell short · ${money(L.cents)}; ${pl(L.lost_n, 'claim')} (${money(L.lost_cents)}) could not be appealed at all.${L.providers_locked ? ' <span class="muted">Which clinicians: open the Providers tab with the owner\'s password.</span>' : ''}</div>
      ${codes.slice(0, 8).map(g=>`<div class="lesson" style="border-top:1px solid var(--line);padding:8px 0">
        <div><b>${esc(g.code ? g.code + ' · ' : '')}${esc(g.label)}</b> <span class="small muted">${pl(g.n, 'note')} · ${money(g.cents)}${g.lost_n ? ` · ${g.lost_n} not appealable (${money(g.lost_cents)})` : ''} · ${esc((g.payers||[]).slice(0,3).map(p=>p.payer_group).join(', '))}</span></div>
        ${(g.providers||[]).length ? `<div class="small muted">Clinicians: ${esc(g.providers.slice(0,5).map(p=>p.name + ' (' + p.n + ')').join(', '))}</div>` : ''}
        <div class="grid two" style="margin-top:4px"><div class="small"><b>What the notes lacked</b><ul>${(g.gaps||[]).map(x=>`<li>${esc(x)}</li>`).join('') || '<li class="muted">Not listed.</li>'}</ul>
          ${(g.tips||[]).length ? `<b>What to document</b><ul>${g.tips.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>` : ''}</div>
          <div class="small"><b>What the note must carry</b><ul>${(g.elements||[]).map(x=>`<li><b>${esc(x.field)}:</b> ${esc(x.what)}</li>`).join('')}</ul></div></div>
        <div class="actions"><button data-tpl="${esc(g.key)}" ${L.review ? '' : 'disabled title="ANTHROPIC_API_KEY is not set"'}>Write the EMA template</button></div><div data-tplout="${esc(g.key)}"></div></div>`).join('')}`
      : '<div class="muted small" style="margin-top:6px">Nothing yet. When Claude\'s review of a note for an appeal finds the note short, what was missing shows here by code.</div>'}`;
  el.querySelectorAll('[data-tpl]').forEach(b => b.onclick = async () => {
    const out = [...el.querySelectorAll('[data-tplout]')].find(x => x.dataset.tplout === b.dataset.tpl);
    b.disabled = true; out.innerHTML = '<div class="muted small">Writing it…</div>';
    try {
      const t = (await post('/api/lessons/template', {key: b.dataset.tpl})).template;
      const text = [t.name, '', ...t.fields.map(f => `${f.required ? '[required] ' : ''}${f.label}: ${f.prompt}\n    why: ${f.why}`), '', 'Attestation: ' + t.attestation, ...(t.notes||[]).map(n => '- ' + n)].join('\n');
      out.innerHTML = `<div class="panel"><b>${esc(t.name)}</b><table class="small"><thead><tr><th>Field</th><th>Required</th><th>What the clinician is asked</th><th>Why the payer needs it</th></tr></thead><tbody>
        ${t.fields.map(f=>`<tr><td>${esc(f.label)}</td><td>${f.required ? 'Yes' : ''}</td><td>${esc(f.prompt)}</td><td class="muted">${esc(f.why)}</td></tr>`).join('')}</tbody></table>
        <div class="small"><b>The clinician signs:</b> ${esc(t.attestation)}</div>${(t.notes||[]).length ? `<ul class="small">${t.notes.map(n=>`<li>${esc(n)}</li>`).join('')}</ul>` : ''}
        <div class="actions"><button data-copy>Copy the template</button></div></div>`;
      out.querySelector('[data-copy]').onclick = () => navigator.clipboard.writeText(text);
    } catch(e){ out.innerHTML = `<div class="small sev-high">${esc(e.message)}</div>`; }
    b.disabled = false;
  });
}
