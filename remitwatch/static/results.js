// RemitWatch Results tab: the money RemitWatch brings back (split by how strongly it can claim it), lift against similar
// unworked items, cost and return, the work done, what became of the open book, hours saved (an estimate), denials by month
// and the daily spot checks. Every number opens the exact list of items behind it, and an item opened from a list walks the
// list (Next, j and k) and comes back to it. Loaded after index.html's helpers (api, esc, money, money2, qs, state, render,
// openDetail); every name here starts with rs or RS_, and resultsTab(v) is the page. It never redraws the whole page for an
// answer or a save: the part that changed is drawn again in place.

const RS_KEY_OF = {recovered: 'returned', lost: 'lost', at_risk: 'at_risk', given_up: 'given_up', spot: 'spot'};
const RS_NAME_KEY = 'rw:results:name';
const RS_PER = 50;

function rsPct(x){ return x == null ? '–' : Math.round(x * 100) + '%'; }
function rsNum(n){ return n == null ? '–' : Number(n || 0).toLocaleString(); }
function rsPl(n, one, many){ return `${rsNum(n)} ${Number(n) === 1 ? one : (many || one + 's')}`; }
// money to the cent (a 40-cent recovery is 40 cents, and rows add up to the figure they open); a large headline may round
// to whole dollars, with the exact amount on hover
function rsM(c){ return c == null ? '–' : (c < 0 ? '-' : '') + money2(Math.abs(c)); }
function rsBig(c){ return c == null ? '–' : (Math.abs(c) >= 10000 ? money(c) : rsM(c)); }
function rsName(){ try { return localStorage.getItem(RS_NAME_KEY) || ''; } catch(e){ return ''; } }
function rsSetName(v){ try { localStorage.setItem(RS_NAME_KEY, v || ''); } catch(e){} }
// the practice's own day (the browser's date), so the day's spot checks do not change in the evening when the server's
// UTC date does; times are shown in the browser's own time
// the practice's day (today.js siteDay, from the brief), the day the server keys the spot checks by; the browser's own
// day only before the brief has loaded (the server keeps the practice's day whatever the page sends)
function rsDay(){ if(typeof siteDay === 'function' && typeof state !== 'undefined' && state.brief && state.brief.today) return siteDay(); const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; }
function rsWhen(iso){ if(!iso) return ''; const d = new Date(iso); return isNaN(d) ? String(iso).slice(0, 16).replace('T', ' ') : d.toLocaleString(undefined, {dateStyle: 'medium', timeStyle: 'short'}); }
function rsDate(iso){ if(!iso) return ''; const d = new Date((typeof practiceDate === 'function' ? practiceDate(iso) : String(iso).slice(0, 10)) + 'T12:00:00'); return isNaN(d) ? String(iso) : d.toLocaleDateString(undefined, {month: 'short', day: 'numeric', year: 'numeric'}); }
function rsMonth(m){ const d = new Date(String(m).slice(0, 7) + '-15T12:00:00'); return isNaN(d) ? String(m) : d.toLocaleDateString(undefined, {month: 'long', year: 'numeric'}); }
function rsLink(key, text, title, note){ return `<a href="#" class="rs-open" data-key="${esc(key)}"${note ? ` data-note="${esc(note)}"` : ''}${title ? ` title="${esc(title)}"` : ''}>${text}</a>`; }
// the owner's password the page holds (typed in the Providers tab or here) rides along on this page's own loads and saves
function rsHeaders(extra){ const h = Object.assign({}, extra || {}); if(state.ownerKey) h['X-Owner-Password'] = state.ownerKey; return h; }
function rsLoad(){ return api('/api/results?' + qs({day: rsDay()}), {headers: rsHeaders()}); }
// every dollar figure says on hover where it comes from (the 835s, EMA's postings, a person's mark, or an estimate)
const RS_BASIS = {
  returned: '835 remittances and EMA postings dated after RemitWatch first flagged the item, plus money it found; a person\'s Paid mark only where no payment record after the flag shows anything',
  'returned:a': '835 remittances and EMA postings after a person\'s work in RemitWatch on the same service, inside the payer\'s answer time for that work',
  'returned:b': 'Money your own systems missed: a second payer never billed (paid by that other payer), an 835 payment EMA never posted, a claim that never left EMA',
  'returned:c': '835 remittances and EMA postings after the flag with no work recorded in RemitWatch; it may have come back anyway',
  taken_back: '835 reversals and EMA takebacks dated after the flag that later money has not paid back',
  lost: 'The items\' own dollars when they expired in Work now',
  at_risk: 'Work now items whose filing date is in the next 14 days, at their dollars at stake',
  given_up: 'What EMA wrote off with no person\'s decision and no owner-approved policy',
  roi: 'Money returned over the monthly costs the owner entered (an estimate of the return)',
  roi_cons: 'Estimate: the share of the money after work that similar unworked items do not explain, plus money found, over the costs',
  lift: '835 remittances and EMA postings on worked and unworked items of the same pattern',
  state: 'Items by state now, from the findings table'};
function rsBasisOf(key){ return RS_BASIS[key] || (String(key || '').startsWith('state:') ? RS_BASIS.state : (String(key || '').startsWith('lift:') ? RS_BASIS.lift : '')); }
// a figure tile: it opens its list when it has one (a dash before the first run opens nothing; it says why on hover)
function rsTile(label, value, sub, key, cls, basis, exact, note){
  const t = [basis || rsBasisOf(key), exact != null ? 'Exactly ' + rsM(exact) : ''].filter(Boolean).join('. ');
  const open = key && value !== '–';
  const why = !open && key ? 'Worked out by the daily run: the first one after this update fills it.' : '';
  const hover = [why, t].filter(Boolean).join(' ');
  return `<div class="kpi rs-tile${cls ? ' ' + cls : ''}${open ? '' : ' rs-static'}"${open ? ` data-key="${esc(key)}" tabindex="0" role="button"` : ''}${note ? ` data-note="${esc(note)}"` : ''}${hover ? ` title="${esc(hover)}"` : ''}><div class="l">${label}</div><div class="v">${value}</div><div class="s">${sub || ''}</div></div>`;
}
async function rsSend(path, method, body){
  const r = await fetch(path, {method, headers: rsHeaders({'Content-Type': 'application/json', 'X-Requested-With': 'remitwatch'}), body: JSON.stringify(body || {})});
  let j = {}; try { j = await r.json(); } catch(e){}
  if(!r.ok){ const err = new Error((j && j.detail) || ('The server did not answer as expected (' + r.status + ').')); err.status = r.status; throw err; }
  return j;
}

// ---------------------------------------------------------------- the titles of the lists (plain words for every key)
function rsTitle(key){
  const d = state.rsData || {}, L = d.labels || {}, ST = L.states || {}, CL = L.closures || {}, WK = L.work || {}, SG = L.signals || {};
  const fixed = {returned: 'Money returned on claims RemitWatch flagged or worked', 'returned:a': 'Money returned after RemitWatch work',
    'returned:b': 'Money found by RemitWatch', 'returned:c': 'Money on flagged items with no recorded work', taken_back: 'Taken back after the flag',
    mark_taken_back: 'Marked Paid, but the payer took the payment back', lost: 'Lost to deadlines', at_risk: 'At risk in the next 14 days',
    given_up: 'Given up without a decision', 'given_up:recoverable': 'Given up without a decision, still inside its window',
    'given_up:past': 'Given up without a decision, past its window', book: 'The open book: every item that was ever in Work now or Waiting',
    'closed:any': 'The open book: closed', 'cant:any': 'The open book: cannot be checked', waiting_no_money: 'Worked, waiting, no money yet',
    denied_again: 'Worked and denied again', 'lift:worked': 'Worked items', 'lift:worked:paid': 'Worked items that brought money',
    'lift:unworked': 'Similar items nobody worked', 'lift:unworked:paid': 'Similar items nobody worked that brought money',
    'lift:z': 'Similar items with no work (the comparison behind "may have come back anyway")', 'lift:z:paid': 'Similar items with no work that were paid',
    'work:any': 'Items with work recorded in RemitWatch', 'work:let_go': 'Let go with a reason', 'work:policy': 'Accepted by the owner\'s policy or a hide rule',
    'hours:investigate': 'Items RemitWatch proved settled or closed by itself', 'hours:packet': 'Items with an appeal packet built',
    'hours:fax': 'Items with a fax sent and followed up by RemitWatch', 'hours:duplicate': 'Items that carry repeat listings folded into them'};
  if(fixed[key]) return fixed[key];
  const [a, b, ...rest] = String(key).split(':'), c = rest.join(':');
  if(a === 'state') return `Items now in ${ST[b] || b}`;
  if(a === 'closed') return `The open book, closed: ${CL[b] || b}`;
  if(a === 'cant') return `Cannot be checked: ${b}${c ? ':' + c : ''}`;
  if(a === 'book' && b === 'open') return `The open book, still open: ${ST[c] || 'still listed, no state yet'}`;
  if(a === 'work' && b === 'person') return `Work by ${c}`;
  if(a === 'work') return `Work done: ${WK[b] || b}`;
  if(a === 'denial_fix') return 'The item where the fix was recorded';
  if(a === 'returned' && b === 'week'){
    const w = (state.rsWeeks || {})[c];
    return `Money returned in the week of ${rsDate(c)}` + (w && w.frozen ? ` (as frozen${w.frozen_at ? ' on ' + rsDate(w.frozen_at) : ''})` : '');
  }
  if(a === 'returned' && b === 'week_now') return `Money returned in the week of ${rsDate(c)}, as read today`;
  if(a === 'returned' && b === 'month') return `Money returned in ${rsMonth(c)}`;
  if(a === 'returned' && b === 'payer') return `Money returned from ${c}`;
  if(a === 'returned' && b === 'signal') return `Money returned on ${SG[c] || c}`;
  if(a === 'returned' && b === 'action') return c === 'found' ? 'Money found by RemitWatch' : c === 'none' ? 'Money with no recorded work' : `Money returned after ${(WK[c] || c).toLowerCase()}`;
  if(a === 'returned' && b === 'person') return `Money returned after work by ${c}`;
  if(a === 'lost' && b === 'week') return `Lost to deadlines in the week of ${rsDate(c)}`;
  return 'Items';
}

// ---------------------------------------------------------------- the list behind a number
// It opens over the page with the keyboard inside it (Tab stays in the window), closes on Escape, Back or ✕ and gives
// the focus back to the figure that opened it. Opening an item makes the panel walk the list (its Next, j and k), and
// closing the panel opens the list again at the same page with the item's row in focus.
function rsListOpen(){ return $('#modal').classList.contains('open') && $('#modalBox').dataset.rs === '1'; }
function rsCloseList(back){ if(!rsListOpen()) return; $('#modal').classList.remove('open'); $('#modalBox').dataset.rs = ''; if(back !== false) rsFocusBack(); }
function rsFocusBack(){ const o = state.rsOpener; state.rsOpener = null; if(o && o.isConnected) o.focus(); }
function rsWatch(){
  if(state.rsWatching) return;
  state.rsWatching = true;
  // the list's window was closed some other way (Escape, a click outside): the focus goes back to the figure
  new MutationObserver(() => { if(!$('#modal').classList.contains('open') && $('#modalBox').dataset.rs === '1'){ $('#modalBox').dataset.rs = ''; if(!state.rsToItem) rsFocusBack(); } })
    .observe($('#modal'), {attributes: true, attributeFilter: ['class']});
  // the item's panel: remember the item it shows; when it closes, the list it came from opens again where it was
  const dr = $('#drawer');
  new MutationObserver(() => {
    if(dr.classList.contains('open')){ if(state.detailId) state.rsLast = state.detailId; return; }
    const L = state.rsList;
    if(!L || !L.waiting || state.tab !== 'results' || rsListOpen()) return;
    L.waiting = false;
    const page = (L.pageOf && state.rsLast && L.pageOf[state.rsLast] != null) ? L.pageOf[state.rsLast] : L.page;
    rsOpenList(L.key, L.title, page, L.note, state.rsLast);
  }).observe(dr, {attributes: true, attributeFilter: ['class'], childList: true});
  // Back (or any change of address) closes the list: it belongs to the page it was opened on
  window.addEventListener('hashchange', () => { if(rsListOpen()) rsCloseList(false); if(state.rsList) state.rsList.waiting = false; });
  // Tab stays inside the list's window while it is open
  $('#modal').addEventListener('keydown', e => {
    if(e.key !== 'Tab' || !rsListOpen()) return;
    const f = [...$('#modalBox').querySelectorAll('a[href], button:not([disabled]), input, select, [tabindex="0"]')].filter(x => x.offsetParent !== null);
    if(!f.length) return;
    const i = f.indexOf(document.activeElement);
    if(e.shiftKey && i <= 0){ e.preventDefault(); f[f.length - 1].focus(); }
    else if(!e.shiftKey && i === f.length - 1){ e.preventDefault(); f[0].focus(); }
  });
}
async function rsOpenList(key, title, page, note, focusId){
  rsWatch();
  page = page || 0;
  // the figure that opened the list gets the focus back when it closes (a list opened again after an item keeps the first one)
  if(!focusId && !rsListOpen() && document.activeElement && !document.activeElement.closest('#modal') && !document.activeElement.closest('#drawer')) state.rsOpener = document.activeElement;
  const box = $('#modalBox'); box.dataset.rs = '1'; $('#modal').classList.add('open');
  box.innerHTML = '<div class="muted">loading…</div>';
  let r;
  try { r = await api('/api/results/list?' + qs({key, page, per: RS_PER})); }
  catch(e){ box.innerHTML = `<div class="dr-head" style="display:flex;justify-content:space-between;gap:8px"><h2 style="margin:0">${esc(title || 'Items')}</h2><button id="rsClose" aria-label="Close">✕</button></div><div class="note">${esc(e.message)}</div>`;
    box.querySelector('#rsClose').onclick = () => rsCloseList(); box.querySelector('#rsClose').focus(); return; }
  const pages = Math.max(1, Math.ceil(r.total / r.per));
  const rows = r.items.map(it => {
    const split = it.returned != null ? `<div class="small muted">${['a','b','c'].filter(b => it[b]).map(b => ({a:'after work', b:'found', c:'no recorded work'}[b] + ' ' + rsM(it[b]))).join(' · ')}${it.taken_back ? ' · taken back ' + rsM(it.taken_back) : ''}</div>` : '';
    const why = (it.events || []).filter(e => e.rule).slice(0, 2).map(e => `<div class="small">${esc(e.rule)}</div>`).join('');
    const where = it.listed ? `<a href="#" class="rs-item" data-id="${esc(it.finding_id)}">${esc(it.label || it.signal || 'Item')}</a>`
      : `${esc(it.label || it.signal || 'Item')} <span class="chip">no longer listed</span>`;
    const st = (state.rsData && state.rsData.labels && state.rsData.labels.states[it.work_state]) || it.work_state || '';
    return `<tr><td>${where}<div class="small muted">${esc(it.payer_group || '')}${it.procedure_code ? ' · ' + esc(it.procedure_code) : ''}${it.service_date ? ' · ' + esc(it.service_date) : ''}${it.claim_id ? ' · claim ' + esc(it.claim_id) : ''}</div>${why}</td>
      <td class="small">${esc(st)}${it.first_flagged ? `<div class="muted">flagged ${esc(rsDate(it.first_flagged))}</div>` : ''}</td>
      <td class="num">${it.cents ? rsM(it.cents) : (it.shortfall_cents ? `<span class="muted">${rsM(it.shortfall_cents)}</span>` : '')}${split}${it.returned == null && it.expected_value_cents != null ? `<div class="small muted" title="${esc(it.recovery_basis || 'Its dollars times its pattern\'s recovery rate')}">likely ${rsM(it.expected_value_cents)}</div>` : ''}</td></tr>`;
  }).join('');
  box.innerHTML = `<div class="dr-head" style="display:flex;justify-content:space-between;gap:8px;align-items:baseline"><h2 style="margin:0">${esc(title || 'Items')}</h2><button id="rsClose" aria-label="Close">✕</button></div>
    ${note ? `<div class="note">${esc(note)}</div>` : ''}
    <div class="note">${rsPl(r.total, 'item')}. Open an item to see its story and why RemitWatch counts it; Next in its panel goes down this list. <a href="/api/results/list.csv?${qs({key})}">Download as CSV</a></div>
    ${r.total ? `<div class="scroll"><table><thead><tr><th>Item</th><th>State</th><th class="num">Dollars</th></tr></thead><tbody>${rows}</tbody></table></div>` : '<div class="muted">No items.</div>'}
    ${pages > 1 ? `<div class="pager"><button id="rsPrev" ${page ? '' : 'disabled'}>Previous</button><span>Page ${page + 1} of ${pages}</span><button id="rsNext" ${page + 1 < pages ? '' : 'disabled'}>Next</button></div>` : ''}`;
  box.querySelector('#rsClose').onclick = () => rsCloseList();
  const prev = box.querySelector('#rsPrev'), next = box.querySelector('#rsNext');
  if(prev) prev.onclick = () => rsOpenList(key, title, page - 1, note);
  if(next) next.onclick = () => rsOpenList(key, title, page + 1, note);
  const listed = r.items.filter(it => it.listed).map(it => it.finding_id);
  box.querySelectorAll('.rs-item').forEach(a => a.onclick = e => { e.preventDefault(); rsWalk(key, title, page, note, listed, r.total, a.dataset.id); });
  const target = focusId && box.querySelector(`.rs-item[data-id="${CSS.escape(focusId)}"]`);
  (target || box.querySelector('#rsClose')).focus();
}
// the panel walks the list: this page's items, and the next pages fetched as Next reaches the end
function rsWalk(key, title, page, note, ids, total, id){
  const pageOf = {}; ids.forEach(x => { pageOf[x] = page; });
  const L = state.rsList = {key, title, page, note, pageOf, next: page + 1, waiting: true};
  state.worklist = ids.slice(); state.wlTotal = total; state.wlKey = 'results:' + key;
  state.wlNext = async () => {
    if(state.wlKey !== 'results:' + key || state.rsList !== L) return 0;
    const more = await api('/api/results/list?' + qs({key, page: L.next, per: RS_PER}));
    const seen = new Set(state.worklist || []);
    const fresh = more.items.filter(it => it.listed && !seen.has(it.finding_id)).map(it => it.finding_id);
    fresh.forEach(x => { L.pageOf[x] = L.next; });
    L.next += 1;
    state.worklist = (state.worklist || []).concat(fresh);
    return fresh.length;
  };
  state.rsToItem = true;
  rsCloseList(false);
  state.rsToItem = false;
  openDetail(id);
}

// ---------------------------------------------------------------- sections
function rsMoneyHtml(d){
  const m = d.money;
  if(!m) return `<div class="panel" id="rs-money"><h2>Money returned</h2><div class="note">${esc(d.note || 'The figures are being worked out. This page fills them in by itself.')}</div></div>`;
  const any = m.anyway || {};
  const enough = any.n != null && any.n >= 20;
  const qual = !m.returned_cents ? 'Nothing has come back on flagged items yet.'
    : (any.cents != null && enough ? `About ${rsM(any.cents)} of this may have come back anyway: similar items with no work were ${rsLink('lift:z:paid', 'paid at ' + rsPct(any.rate), '', 'These are the similar items with no work that were paid: the share of them is the rate.')} (${rsLink('lift:z', rsPl(any.n, 'item'), '', 'These are the similar items with no work the rate is measured on.')}).`
      : `Some of this may have come back anyway. Not enough similar items yet to say how much (${rsNum(any.n || 0)} of the 20 needed).`);
  return `<div class="panel" id="rs-money" style="margin-top:0">
    <div class="panel-head"><h2>Money returned on claims RemitWatch flagged or worked</h2><span class="small muted">since ${esc(rsDate(m.first_flag) || 'the first flag')}</span></div>
    <div class="rs-top">${rsLink('returned', `<span class="rs-big">${rsBig(m.returned_cents)}</span>`, RS_BASIS.returned + '. Exactly ' + rsM(m.returned_cents))}
      <span class="muted">on ${rsPl(m.items, 'item')}</span></div>
    <div class="note">${enough ? qual : esc(qual)}</div>
    <div class="grid kpis">
      ${rsTile('After RemitWatch work', rsBig(m.a_cents), rsPl(m.a_items, 'item') + ' paid inside the payer\'s answer time after the work', 'returned:a', '', '', m.a_cents)}
      ${rsTile('Found by RemitWatch', rsBig(m.b_cents), rsPl(m.b_items, 'item') + ' your own systems missed', 'returned:b', '', '', m.b_cents)}
      ${rsTile('Flagged, no recorded work', rsBig(m.c_cents), rsPl(m.c_items, 'item') + '; may have come back anyway', 'returned:c', '', '', m.c_cents)}
      ${m.taken_back_cents ? rsTile('Taken back after the flag', rsBig(m.taken_back_cents), rsPl(m.taken_back_items, 'item') + ' not paid back since', 'taken_back', '', '', m.taken_back_cents) : ''}
    </div>
    ${m.above_flagged_cents ? `<div class="small" style="margin-top:6px">${rsLink('above_flagged', rsPl(m.above_flagged_items, 'item'))} paid ${rsM(m.above_flagged_cents)} more than the amount flagged: counted above. Each item's own recovered figure stops at the amount flagged.</div>` : ''}
    ${m.mark_taken_back_items ? `<div class="small" style="margin-top:6px">${rsLink('mark_taken_back', rsPl(m.mark_taken_back_items, 'item'))} marked Paid by a person, but the payer took the payment back: not counted.</div>` : ''}
    <details class="small" style="margin-top:8px"><summary>How this is counted</summary>
      <p>Every dollar a payer paid on an item's line after RemitWatch first flagged the item, plus money RemitWatch found. Money paid before the flag never counts, and money the payer paid before the flag and took back after it is paid back first by any later payment: only what comes beyond it counts. One payment counts once, on the earliest item of its line.</p>
      <p><b>After RemitWatch work</b>: paid after a fax, a claim or appeal marked Sent, a call or portal check, a payment marked posted or a bill to the patient or second payer recorded in RemitWatch on the same service, and inside that payer's usual answer time. <b>Found by RemitWatch</b>: money from a second payer nobody had billed, an 835 payment EMA never posted, a claim that never left EMA. <b>Flagged, no recorded work</b>: everything else paid after the flag. A payment the payer later took back is not counted, and a refund, fee or unposted-payment item marked Paid never is.</p>
      ${m.median_days_work_to_money != null ? `<p>From the work to the money: ${Math.round(m.median_days_work_to_money)} days at the middle.</p>` : ''}
    </details></div>`;
}

function rsScoreHtml(s){
  if(!s) return '<div class="panel" id="rs-score"><h2>Week by week</h2><div class="note">The week by week figures could not be read just now. Reload the page in a minute.</div></div>';
  const w = (s.weeks || []).slice(-12).reverse();
  state.rsWeeks = {}; (s.weeks || []).forEach(x => { state.rsWeeks[x.week] = x; });
  const gu = s.given_up_check ? `<div class="small muted" style="margin-top:4px">The self-check counts ${rsM(s.given_up_check.cents)} on ${rsPl(s.given_up_check.n, 'item')} by its own rule (written off at posting, any kind of item): a cross-check, its list is not kept.</div>` : '';
  const cell = (r) => {
    const frozen = r.frozen ? ` title="${esc('Frozen' + (r.frozen_at ? ' on ' + rsDate(r.frozen_at) : '') + ': this figure never changes')}"` : '';
    const main = r.recovered_cents ? `<span${frozen}>${rsLink('returned:week:' + r.week, rsM(r.recovered_cents))}</span>` : rsM(0);
    const now = r.now_cents != null ? ` <span class="small muted">(now ${r.now_cents ? rsLink('returned:week_now:' + r.week, rsM(r.now_cents), 'This week\'s money as read today: money dated in it that came or was taken back after it was frozen') : rsM(0)})</span>` : '';
    return main + now;
  };
  return `<div class="panel" id="rs-score"><h2>Week by week</h2>
    <div class="grid kpis">
      ${rsTile('Recovered', s.recovered_cents == null ? '–' : rsBig(s.recovered_cents), s.counts.recovered == null ? 'after the first run' : rsPl(s.counts.recovered, 'item'), 'returned', '', '', s.recovered_cents)}
      ${rsTile('Lost to deadlines', s.lost_cents == null ? '–' : rsBig(s.lost_cents), s.counts.lost == null ? 'after the first run' : rsPl(s.counts.lost, 'item') + ' expired in Work now', 'lost', '', '', s.lost_cents)}
      ${rsTile('At risk in 14 days', rsBig(s.at_risk_cents), rsPl(s.counts.at_risk, 'item') + ' in Work now' + (s.at_risk_expected_cents != null ? `; about ${rsM(s.at_risk_expected_cents)} likely to come back if worked` : ''), 'at_risk', '', s.at_risk_expected_cents != null ? RS_BASIS.at_risk + '. Likely: ' + ((s.basis && s.basis.at_risk_expected) || '') : '', s.at_risk_cents)}
      ${rsTile('Given up without a decision', s.given_up_cents == null ? '–' : rsBig(s.given_up_cents), (s.counts.given_up == null ? '' : rsPl(s.counts.given_up, 'item') + '. ') + (s.given_up_source === 'selfcheck' ? 'From the self-check.' : 'Worked out here.'), 'given_up', '', (s.basis && s.basis.given_up) || '', s.given_up_cents)}
    </div>${gu}
    ${w.length ? `<div class="scroll"><table><thead><tr><th>Week of</th><th class="num">Recovered</th><th class="num">Lost</th><th class="num">At risk</th><th class="num" title="${esc('Given up: ' + (s.given_up_weeks || ''))}">Given up</th></tr></thead><tbody>
      ${w.map(r => `<tr><td>${esc(rsDate(r.week))}${r.frozen ? '' : ' <span class="small muted">still moving</span>'}</td><td class="num">${cell(r)}</td><td class="num">${r.lost_cents ? rsLink('lost:week:' + r.week, rsM(r.lost_cents)) : rsM(0)}</td><td class="num">${r.at_risk_cents == null ? '–' : rsM(r.at_risk_cents)}</td><td class="num">${rsM(r.given_up_cents)}</td></tr>`).join('')}
    </tbody></table></div>` : ''}
    <div class="note">Weeks freeze two weeks after they end, so a trend never changes after the fact: a frozen week opens the items it counted, with today's reading beside it where money moved since. Newer weeks say "still moving".</div></div>`;
}

function rsLiftHtml(d){
  const l = d.lift; if(!l) return '';
  const enough = l.enough;
  return `<div class="panel" id="rs-lift"><h2>Did the work make the difference?</h2>
    <div class="grid kpis">
      ${rsTile('Worked items paid', rsPct(l.worked.rate), `${rsNum(l.worked.paid)} of ${rsPl(l.worked.n, 'item')}`, 'lift:worked:paid')}
      ${rsTile('Similar unworked items paid', rsPct(l.unworked.rate), `${rsNum(l.unworked.paid)} of ${rsPl(l.unworked.n, 'item')}`, 'lift:unworked:paid')}
      ${rsTile('Money the work added', enough ? rsM(l.incremental_cents) : 'not enough cases yet', `worked items brought ${rsM(l.worked.returned_cents)}; the unworked rate predicts ${rsM(l.predicted_cents)} (an estimate)`, 'lift:worked', '', '', null,
        `These are the worked items: together they brought ${rsM(l.worked.returned_cents)}. Similar items nobody worked were paid at a rate that predicts ${rsM(l.predicted_cents)} of it; the difference, ${rsM(l.incremental_cents)}, is what the work added (an estimate).`)}
    </div>
    <div class="note">${enough ? esc(l.basis) : 'Not enough cases yet: these rates need 20 worked and 20 unworked items. ' + esc(l.basis)} An item counts as worked only by work done before its money came.</div></div>`;
}

function rsRoiHtml(d){
  const r = d.roi; if(!r) return '<div id="rs-roi"></div>';
  const cons = r.enough ? (r.roi_conservative == null ? '–' : r.roi_conservative + '×') : 'not enough cases yet';
  const head = r.costs_set ? `<div class="grid kpis">
      ${rsTile('Return on cost', r.roi == null ? '–' : r.roi + '×', `${rsBig(r.returned_cents)} returned for ${rsBig(r.cost_cents)} spent`, 'returned', '', RS_BASIS.roi)}
      ${rsTile('Return counting only what the work added', cons, r.enough ? `counting only what the work added, plus money found: ${rsM(r.conservative_cents)}` : 'needs 20 worked and 20 unworked items', r.enough ? 'returned:a' : '', '', RS_BASIS.roi_cons, null,
        `This list is the money after RemitWatch work. The return counting only what the work added keeps ${Math.round((r.factor || 0) * 100)}% of it (the share similar unworked items do not explain), adds the money found, and divides by the costs.`)}
    </div>` : `<div class="note">Enter what RemitWatch costs each month (below, under Costs) to see the return on it.</div>`;
  const added = x => x.roi_conservative == null ? '–' : x.roi_conservative + '×';
  const rows = (r.months || []).slice(-12).reverse().map(x => `<tr><td>${esc(rsMonth(x.month))}<div class="small muted rs-narrow">${rsNum(x.fax_pages)} pages, ${rsNum(x.ai_calls)} AI calls${r.enough ? '; only what the work added: ' + added(x) : ''}</div></td><td class="num">${x.returned_cents ? rsLink('returned:month:' + x.month, rsM(x.returned_cents)) : rsM(0)}</td><td class="num">${rsM(x.cost_cents)}</td><td class="num">${x.roi == null ? '–' : x.roi + '×'}</td><td class="num rs-wide">${added(x)}</td><td class="num small rs-wide">${rsNum(x.fax_pages)} pages, ${rsNum(x.ai_calls)} AI calls</td></tr>`).join('');
  return `<div class="panel" id="rs-roi"><h2>Cost and return</h2>${head}
    ${rows ? `<div class="scroll"><table><thead><tr><th>Month</th><th class="num">Returned</th><th class="num">Cost</th><th class="num">Return</th><th class="num rs-wide">Only what the work added</th><th class="num rs-wide">Counted by the app</th></tr></thead><tbody>${rows}</tbody></table></div>` : ''}
    <div class="note">${esc(r.basis)}</div></div>`;
}

function rsWorkHtml(d){
  const w = d.work; if(!w) return '';
  const kinds = (w.kinds || []).map(k => `<tr><td>${esc(k.label)}</td><td class="num">${rsLink('work:' + k.key, rsNum(k.items))}</td><td class="num">${rsNum(k.n)}</td></tr>`).join('');
  const people = (w.people || []).slice(0, 12).map(p => `<tr><td>${esc(p.key)}</td><td class="num">${rsLink('work:person:' + p.key, rsNum(p.items))}</td><td class="num">${rsNum(p.n)}</td></tr>`).join('');
  const costs = (d.settings && d.settings.values && d.settings.values.costs) || {};
  const faxCost = Math.round((w.fax_pages || 0) * (costs.fax_page || 0) * 100);
  return `<div class="panel" id="rs-work"><h2>Work done in RemitWatch</h2>
    <div class="grid two"><div><table><thead><tr><th>Kind of work</th><th class="num">Items</th><th class="num">Times</th></tr></thead><tbody>${kinds || '<tr><td colspan="3" class="muted">No work recorded yet.</td></tr>'}</tbody></table>
      <div class="small" style="margin-top:6px">Faxes sent: ${rsNum(w.faxes_sent)} (${rsNum(w.fax_pages)} pages${faxCost ? ', ' + rsM(faxCost) : ''}).
      Let go with a reason: ${rsLink('work:let_go', rsNum(w.let_go))}. Accepted by the owner's policy or a hide rule: ${rsLink('work:policy', rsNum(w.policy))}.</div></div>
    <div><table><thead><tr><th>Who</th><th class="num">Items</th><th class="num">Times</th></tr></thead><tbody>${people || '<tr><td colspan="3" class="muted">No one yet.</td></tr>'}</tbody></table></div></div></div>`;
}

function rsStatesHtml(d){
  const st = (d.live || {}).states || {}, L = (d.labels && d.labels.states) || {};
  return Object.keys(L).map(k => rsTile(esc(L[k]), rsNum((st[k] || {}).n), rsM((st[k] || {}).cents), 'state:' + k)).join('');
}
function rsOutcomesHtml(d){
  const o = d.outcomes;
  const now = `<div class="small muted" style="margin-top:10px">Every item by its state right now (all items, not only the open book):</div><div class="grid kpis">${rsStatesHtml(d)}</div>`;
  if(!o) return `<div class="panel" id="rs-book"><h2>The items by state</h2>${now}</div>`;
  const since = d.first_run ? ` since the first run on ${esc(rsDate(d.first_run))}` : '';
  const open = (o.open_by || []).map(c => `<tr><td>${esc(c.label)}</td><td class="num">${rsLink('book:open:' + c.key, rsNum(c.n))}</td></tr>`).join('');
  const closed = (o.closed_by || []).map(c => `<tr><td>${esc(c.label)}</td><td class="num">${rsLink('closed:' + c.key, rsNum(c.n))}</td><td class="num">${rsPct(c.rate)}</td></tr>`).join('');
  const cant = (o.cant_by || []).map(c => `<tr><td>${esc(c.key)}</td><td class="num">${rsLink('cant:' + c.key, rsNum(c.n))}</td></tr>`).join('');
  const wins = (o.win_by_kind || []).map(k => `<tr><td>${esc(k.label)}</td><td class="num">${rsNum(k.won)}</td><td class="num">${rsNum(k.lost)}</td><td class="num">${rsPct(k.rate)}</td></tr>`).join('');
  return `<div class="panel" id="rs-book"><h2>The open book</h2>
    <div class="note">Every item that was ever in Work now or Waiting${since}: ${rsLink('book', rsNum(o.book))}. Still open, closed and cannot be checked add up to it.</div>
    <div class="grid two" style="margin-top:6px"><div>
      <div><b>Still open</b> (${rsNum(o.still_open)}):</div>
      <table><tbody>${open || '<tr><td class="muted">None.</td></tr>'}</tbody></table>
      <div style="margin-top:8px"><b>${rsPct(o.closed_rate)}</b> closed${since} (${rsLink('closed:any', rsNum(o.closed))}):</div>
      <table><tbody>${closed || '<tr><td class="muted">None yet.</td></tr>'}</tbody></table>
      <div style="margin-top:8px"><b>${rsPct(o.cant_rate)}</b> cannot be checked (${rsLink('cant:any', rsNum(o.cant_check))}), because:</div>
      <table><tbody>${cant || '<tr><td class="muted">None.</td></tr>'}</tbody></table></div>
    <div>
      <div>Worked and waiting with no money yet: ${rsLink('waiting_no_money', rsNum(o.waiting_no_money))} (${rsPct(o.waiting_no_money_rate)} of ${rsPl(o.worked, 'worked item')}${o.waiting_median_days != null ? `; ${Math.round(o.waiting_median_days)} days since the work, at the middle` : ''}).</div>
      <div style="margin-top:4px">Worked and denied again: ${rsLink('denied_again', rsNum(o.denied_again))} (${rsPct(o.denied_again_rate)}).</div>
      ${wins ? `<table style="margin-top:8px"><thead><tr><th>Kind of work</th><th class="num">Won</th><th class="num">Denied again</th><th class="num">Win rate</th></tr></thead><tbody>${wins}</tbody></table>` : ''}
    </div></div>${now}</div>`;
}

function rsUnlockHtml(d, what){
  if(d.owner_open || !d.owner_locked) return '';
  return `<form class="rs-unlock filters" style="margin-top:6px"><span class="small">The owner sets the ${what}. Owner:</span> <input type="password" autocomplete="one-time-code" data-1p-ignore data-lpignore="true" aria-label="owner's password" placeholder="owner's password" style="width:12em"> <button class="primary">Open</button> <span class="small sev-medium rs-unlock-msg" role="alert"></span></form>`;
}
function rsHoursHtml(d){
  const h = d.hours; if(!h) return '<div id="rs-hours"></div>';
  const can = d.owner_open;
  const rows = h.rows.map(r => {
    const count = r.n == null ? '–' : (r.items != null && r.items !== r.n ? `${rsNum(r.n)} on ${rsLink('hours:' + r.key, rsPl(r.items, 'item'))}` : rsLink('hours:' + r.key, rsNum(r.n)));
    return `<tr><td>${esc(r.label)}<div class="small muted rs-narrow">${r.hours == null ? '' : rsNum(r.hours) + ' hours'}</div></td><td class="num">${count}</td>
      <td class="num">${can ? `<input type="number" min="0" max="600" step="1" class="rs-min" data-k="${esc(r.key)}" value="${esc(r.minutes)}" style="width:5em" aria-label="${esc(((d.labels || {}).minutes || {})[r.key] || 'Minutes')}">` : esc(r.minutes)}</td><td class="num rs-wide">${r.hours == null ? '–' : rsNum(r.hours)}</td></tr>`;
  }).join('');
  return `<div class="panel" id="rs-hours"><h2>Hours saved: ${h.hours == null ? '–' : rsNum(h.hours)} <span class="small muted">(an estimate)</span></h2>
    <div class="scroll"><table><thead><tr><th>What RemitWatch did</th><th class="num">How many</th><th class="num">Minutes each</th><th class="num rs-wide">Hours</th></tr></thead><tbody>${rows}
    <tr><td><b>Total</b></td><td></td><td></td><td class="num rs-wide"><b>${h.hours == null ? '–' : rsNum(h.hours)}</b></td></tr></tbody></table></div>
    ${h.hours == null ? '<div class="note">Worked out by the daily run: the first one after this update fills these counts.</div>' : ''}
    <div class="note">Each thing counts at the minutes in force when it happened: a changed figure prices what happens from then on, and the earlier figures stay in the history.</div>
    ${can ? '<div class="actions" style="margin-top:6px"><button class="primary" id="rsSaveMin">Save minutes</button><span class="small muted rs-msg" id="rsMinMsg" role="status"></span></div>'
      : (rsUnlockHtml(d, 'minutes') || '<div class="note">The owner sets the minutes.</div>')}</div>`;
}

function rsDenialsHtml(d){
  const x = d.denials; if(!x || !(x.months || []).length) return '';
  const months = x.months.slice(-12).reverse().map(m => `<tr><td>${esc(rsMonth(m.month))}</td><td class="num">${rsPct(m.line_rate)}</td><td class="num">${rsNum(m.denied)} of ${rsNum(m.answered)}</td><td class="num">${rsPct(m.dollar_rate)}</td></tr>`).join('');
  const groups = (x.by_group || []).slice(0, 10).map(g => {
    const last = (g.months || []).slice(-6).map(m => rsPct(m.line_rate)).join(' · ');
    return `<tr><td>${esc(g.key)}</td><td class="num">${rsPct(g.line_rate)}</td><td class="small">${esc(last)}</td></tr>`; }).join('');
  const reasons = r => (r || []).map(z => `<li>${esc(z.reason)} ${esc(z.meaning ? '(' + z.meaning.slice(0, 80) + ')' : '')}: ${rsNum(z.before)} to ${rsNum(z.recent)} lines</li>`).join('');
  const marks = (x.markers || []).map((mk, i) => `<li>${esc(rsDate(mk.date))}: ${esc(mk.kind)} fix for ${esc(mk.payer_group || 'a payer')} (${rsLink('denial_fix:' + i, 'the item')}). Denied before: ${rsPct(mk.before.line_rate)}; after: ${rsPct(mk.after.line_rate)}.</li>`).join('');
  return `<div class="panel" id="rs-denials"><h2>Denials, month by month</h2><div class="note">${esc(x.definition || '')}</div>
    <div class="grid two"><div class="scroll"><table><thead><tr><th>Month</th><th class="num">Lines denied</th><th class="num">Lines</th><th class="num">Dollars denied</th></tr></thead><tbody>${months}</tbody></table></div>
    <div class="scroll"><table><thead><tr><th>Payer group</th><th class="num">Denied</th><th>Last six months</th></tr></thead><tbody>${groups}</tbody></table></div></div>
    <div class="grid two" style="margin-top:8px"><div><b>Rising reasons</b> (last 90 days against the 90 before)<ul class="small">${reasons(x.reasons && x.reasons.rising) || '<li class="muted">None.</li>'}</ul></div>
    <div><b>Falling reasons</b><ul class="small">${reasons(x.reasons && x.reasons.falling) || '<li class="muted">None.</li>'}</ul></div></div>
    ${marks ? `<div><b>Fixes recorded</b><ul class="small">${marks}</ul></div>` : '<div class="note">When someone records a credentialing, authorization or coding fix on an item, it shows here with the rate before and after.</div>'}
    <div class="small"><a href="#" id="rsClin">Denials by clinician</a> (the owner's password, when one is set)</div><div id="rsClinBox"></div></div>`;
}

function rsSpotChip(p, reasons){
  return p.answer ? `<span class="chip">${p.answer === 'agree' ? 'Agreed' : 'Disagreed: ' + esc(reasons[p.reason] || p.reason || '')}</span> <span class="small muted">${esc(p.who || '')}</span>` : '';
}
function rsSpotSummaryHtml(s){
  s = s || {};
  const strata = (s.by_stratum || []).map(r => `<tr><td>${esc(r.label)}</td><td class="num">${rsPct(r.rate)}</td><td class="num">${rsNum(r.agree)} of ${rsNum(r.n)}</td></tr>`).join('');
  const weeks = (s.by_week || []).slice(-8).reverse().map(r => `<tr><td>${esc(rsDate(r.key))}</td><td class="num">${rsPct(r.rate)}</td><td class="num">${rsNum(r.agree)} of ${rsNum(r.n)}</td></tr>`).join('');
  const L = ((state.rsData || {}).labels || {}).signals || {};
  const dis = (s.disagreements || []).slice(-10).reverse().map(x => `<li>${esc(rsDate(x.day))}: <a href="#" class="rs-item" data-id="${esc(x.finding_id)}">${esc(L[x.signal] || x.signal || 'item')}</a>, ${esc(x.reason_label || x.reason || '')}${x.note ? ': ' + esc(x.note) : ''}</li>`).join('');
  return `<div class="grid two" style="margin-top:10px"><div><b>${rsPct(s.rate)}</b> agreement (${rsNum(s.agree)} of ${rsNum(s.answered)} answered)
      <table><thead><tr><th>Picked from</th><th class="num">Agreed</th><th class="num">Checks</th></tr></thead><tbody>${strata || '<tr><td colspan="3" class="muted">No answers yet.</td></tr>'}</tbody></table></div>
    <div><table><thead><tr><th>Week of</th><th class="num">Agreed</th><th class="num">Checks</th></tr></thead><tbody>${weeks || '<tr><td colspan="3" class="muted">No answers yet.</td></tr>'}</tbody></table></div></div>
    ${dis ? `<div style="margin-top:8px"><b>Recent disagreements</b> (each goes to the people who fix the rules)<ul class="small">${dis}</ul></div>` : ''}`;
}
function rsSpotHtml(d){
  const sp = d.spot || {}, reasons = sp.reasons || {};
  const L = (d.labels && d.labels.signals) || {};
  const items = (sp.today || []).map(p => `<div class="change rs-spot" data-id="${esc(p.finding_id)}" data-day="${esc(p.day || d.day || d.today)}"><div>
      <a href="#" class="rs-item" data-id="${esc(p.finding_id)}">${esc(L[p.signal] || p.signal || 'Item')}</a>
      <div class="small muted">${esc((sp.strata || {})[p.stratum] || p.stratum)} · ${esc(p.payer_group || '')} · ${rsM(p.cents)}</div>
      ${p.basis ? `<div class="small">${esc(p.basis)}</div>` : ''} <span class="rs-done">${rsSpotChip(p, reasons)}</span>
      <div class="rs-dis" hidden><select class="rs-reason" aria-label="What is wrong"><option value="">Pick what is wrong</option>${Object.entries(reasons).map(([k, v]) => `<option value="${esc(k)}">${esc(v)}</option>`).join('')}</select>
        <input class="rs-note" maxlength="500" placeholder="What did you find? (optional)" style="width:min(100%,22em)"> <button class="rs-send" disabled>Save</button></div>
      <div class="small sev-high rs-err" role="alert"></div></div>
      <div class="actions"${p.answer ? ' hidden' : ''}><button class="rs-agree">Agree</button><button class="rs-disagree">Disagree</button></div></div>`).join('');
  return `<div class="panel" id="rs-spot"><div class="panel-head"><h2>Spot checks</h2><span class="small muted">check each against EMA and the payer, then say if RemitWatch is right</span></div>
    ${sp.error ? `<div class="warnbox">${esc(sp.error)}</div>` : ''}
    <div class="small" style="margin-bottom:6px"><label>Your name <input id="rsWho" maxlength="80" value="${esc(rsName())}" placeholder="optional" style="width:12em"></label></div>
    ${items || '<div class="muted">No item to check today.</div>'}
    <div id="rsSpotSummary">${rsSpotSummaryHtml(sp.summary)}</div></div>`;
}

function rsCostsHtml(d){
  const s = d.settings || {}, v = (s.values || {}).costs || {}, labels = d.cost_labels || {};
  const can = d.owner_open;
  const rows = Object.keys(labels).map(k => `<tr><td>${esc(labels[k])}</td><td class="num">${can ? `$<input type="number" min="0" step="0.01" class="rs-cost" data-k="${esc(k)}" value="${esc(v[k])}" style="width:7em" aria-label="${esc(labels[k])}">` : '$' + esc(v[k])}</td></tr>`).join('');
  const hist = (s.history || []).slice(-8).reverse().map(h => `<li>${esc(rsWhen(h.at))} by ${esc(h.by || 'someone')}</li>`).join('');
  return `<div class="panel" id="rs-costs"><h2>Costs</h2>
    <table><tbody>${rows}</tbody></table>
    ${can ? '<div class="actions" style="margin-top:6px"><button class="primary" id="rsSaveCost">Save costs</button><span class="small muted" id="rsCostMsg" role="status"></span></div>'
      : (rsUnlockHtml(d, 'costs') || '<div class="note">The owner sets the costs.</div>')}
    ${hist ? `<details class="small"><summary>Earlier versions</summary><ul>${hist}</ul></details>` : ''}</div>`;
}

// ---------------------------------------------------------------- the page
function rsStyle(){
  if(document.getElementById('rsStyle')) return;
  const el = document.createElement('style'); el.id = 'rsStyle';
  el.textContent = '.rs-top{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap;margin:4px 0 2px}.rs-big{font-size:34px;font-weight:750;line-height:1.1}'
    + '.rs-top a{text-decoration:none;color:var(--ink)}.rs-tile:focus-visible{outline:2px solid var(--accent)}.rs-static{cursor:default}.rs-spot .actions{flex:0 0 auto}'
    + '.rs-narrow{display:none}.rs-due{display:flex;gap:8px;align-items:center;flex-wrap:wrap}'
    + '@media(max-width:640px){.rs-big{font-size:28px}.rs-spot{flex-wrap:wrap}.rs-wide{display:none}.rs-narrow{display:block}}';
  document.head.appendChild(el);
}
function rsDueHtml(d){
  const due = (d.spot || {}).due || {};
  if(!due.due) return '';
  return `<div class="panel rs-due" id="rs-due" style="margin-top:0"><b>${rsPl(due.due, 'spot check')} for today</b><span class="small muted">about ${rsPl(due.due, 'minute')}: check each against EMA and the payer, then say if RemitWatch is right</span><button id="rsGoSpot">Check them</button></div>`;
}
function rsDraw(v, d, score){
  const head = d.as_of ? `Figures as of ${esc(rsWhen(d.as_of))}.` : 'Not worked out yet.';
  const upd = d.updating ? ` <span class="sev-medium">Updating after the analysis of ${esc(rsWhen(d.detect_run))}: reload in a minute to see the new figures.</span>` : '';
  v.innerHTML = `<div class="page-head"><h1 class="page-title">Results</h1><span class="small muted">${head}${upd}</span></div>
    <div id="rsBoard"></div>
    ${(d.errors || []).length ? `<div class="warnbox">Part of these figures could not be worked out: ${esc(d.errors[0])}</div>` : ''}
    ${d.update_error ? `<div class="warnbox">The figures could not be brought up to date (${esc(d.update_error)}): these are the last ones worked out.</div>` : ''}
    ${rsDueHtml(d)}${rsMoneyHtml(d)}${rsRoiHtml(d)}${rsScoreHtml(score)}${rsLiftHtml(d)}${rsWorkHtml(d)}${rsOutcomesHtml(d)}${rsHoursHtml(d)}${rsDenialsHtml(d)}${rsSpotHtml(d)}${rsCostsHtml(d)}`;
  rsBind(v, d);
  // the money scoreboard and the items by state, at the top (moved here from Today: static/today.js tdBoard)
  if(typeof tdBoard === 'function') tdBoard(v.querySelector('#rsBoard'));
}
// one panel drawn again in place (after a save or an unlock), never the whole page
function rsRedraw(v, id, html){
  const el = v.querySelector('#' + id); if(!el) return;
  const tmp = document.createElement('div'); tmp.innerHTML = html;
  const fresh = tmp.firstElementChild; if(!fresh) return;
  el.replaceWith(fresh);
  rsBind(fresh, state.rsData);
}

async function resultsTab(v){
  rsStyle(); rsWatch();
  // both answers are asked for at once; each part is drawn as soon as what it needs has come
  const [dr, sr] = await Promise.allSettled([rsLoad(), api('/api/results/scoreboard?' + qs({day: rsDay()}))]);
  if(v !== $('#view') && !v.isConnected) return;
  const score = sr.status === 'fulfilled' ? sr.value : null;
  if(dr.status !== 'fulfilled'){
    v.innerHTML = `<div class="page-head"><h1 class="page-title">Results</h1></div><div id="rsBoard"></div><div class="warnbox">The figures could not be read just now (${esc(dr.reason && dr.reason.message || 'no answer')}). Reload the page in a minute.</div>${rsScoreHtml(score)}`;
    rsBind(v, {});
    if(typeof tdBoard === 'function') tdBoard(v.querySelector('#rsBoard'));
    return;
  }
  const d = dr.value;
  // a password the page holds that the site no longer takes: stop sending it (each wrong one is counted)
  if(state.ownerKey && d.owner_locked && !d.owner_open && typeof pvForget === 'function') pvForget();
  state.rsData = d;
  rsDraw(v, d, score);
  if(!d.ready && d.host === 'server') setTimeout(async () => {      // the first working out finishes: draw it once
    if(state.tab !== 'results' || !v.isConnected) return;
    try { const again = await rsLoad(); if(again.ready && state.tab === 'results' && v.isConnected){ state.rsData = again; rsDraw(v, again, score); } } catch(e){}
  }, 15000);
  if(state.resultsOpen){
    const k = RS_KEY_OF[state.resultsOpen] || state.resultsOpen; state.resultsOpen = null;
    if(k === 'spot'){ const s = v.querySelector('#rs-spot'); if(s) s.scrollIntoView({block: 'start'}); const b = v.querySelector('.rs-spot .actions:not([hidden]) .rs-agree'); if(b) b.focus(); }
    else rsOpenList(k, rsTitle(k));
  }
}

function rsBind(v, d){
  d = d || {};
  v.querySelectorAll('.rs-open').forEach(a => a.onclick = e => { e.preventDefault(); rsOpenList(a.dataset.key, rsTitle(a.dataset.key), 0, a.dataset.note || ''); });
  v.querySelectorAll('.rs-tile[data-key]').forEach(t => {
    const go = () => rsOpenList(t.dataset.key, rsTitle(t.dataset.key), 0, t.dataset.note || '');
    t.onclick = go; t.onkeydown = e => { if(e.key === 'Enter' || e.key === ' '){ e.preventDefault(); go(); } }; });
  v.querySelectorAll('.rs-item').forEach(a => a.onclick = e => { e.preventDefault(); openDetail(a.dataset.id); });
  const go = v.querySelector('#rsGoSpot'); if(go) go.onclick = () => { const s = $('#rs-spot'); if(s) s.scrollIntoView({block: 'start'}); const b = document.querySelector('.rs-spot .actions:not([hidden]) .rs-agree'); if(b) b.focus(); };
  const who = v.querySelector('#rsWho') || document.querySelector('#rsWho');      // a panel drawn again in place keeps the page's own name box
  if(who) who.onchange = () => rsSetName(who.value.trim());
  v.querySelectorAll('.rs-spot').forEach(row => {
    const err = row.querySelector('.rs-err'), sel = row.querySelector('.rs-reason'), sendBtn = row.querySelector('.rs-send');
    const send = async (answer, reason, note) => {
      err.textContent = '';
      const btns = row.querySelectorAll('button'); btns.forEach(b => { b.disabled = true; });
      try {
        const r = await rsSend('/api/results/spot', 'POST', {day: row.dataset.day, finding_id: row.dataset.id, answer, reason, note, name: (who && who.value.trim()) || ''});
        // the row says what was recorded and the rates are drawn again in place: the page stays where it is
        row.querySelector('.rs-done').innerHTML = rsSpotChip({answer, reason, who: r.who}, ((d.spot || {}).reasons) || {});
        row.querySelector('.rs-dis').hidden = true; row.querySelector('.actions').hidden = true;
        const sum = document.querySelector('#rsSpotSummary'); if(sum && r.summary){ sum.innerHTML = rsSpotSummaryHtml(r.summary); rsBind(sum, d); }
        const due = document.querySelector('#rs-due'); const left = document.querySelectorAll('.rs-spot .actions:not([hidden])').length;
        if(due){ if(!left) due.remove(); else { const b = due.querySelector('b'); if(b) b.textContent = rsPl(left, 'spot check') + ' for today'; } }
        const nextBtn = document.querySelector('.rs-spot .actions:not([hidden]) .rs-agree'); if(nextBtn) nextBtn.focus();
      } catch(e){ err.textContent = e.message; }
      finally { btns.forEach(b => { b.disabled = false; }); if(sendBtn) sendBtn.disabled = !sel.value; }
    };
    row.querySelector('.rs-agree').onclick = () => send('agree');
    row.querySelector('.rs-disagree').onclick = () => { row.querySelector('.rs-dis').hidden = false; sel.focus(); };
    sel.onchange = () => { sendBtn.disabled = !sel.value; };
    sendBtn.onclick = () => { if(!sel.value){ err.textContent = 'Pick what is wrong first.'; return; } send('disagree', sel.value, row.querySelector('.rs-note').value.trim()); };
  });
  const root = document.querySelector('#view') || v;
  const saveVals = async (group, sel, msgId) => {
    const vals = {}; v.querySelectorAll(sel).forEach(i => { vals[i.dataset.k] = Number(i.value); });
    const msg = v.querySelector('#' + msgId);
    if(msg){ msg.className = 'small muted'; msg.textContent = 'Saving…'; }
    try {
      await rsSend('/api/results/settings', 'PUT', {values: {[group]: vals}, base_version: (d.settings || {}).version || '', name: rsName()});
      const fresh = await rsLoad(); state.rsData = fresh;
      rsRedraw(root, 'rs-hours', rsHoursHtml(fresh)); rsRedraw(root, 'rs-roi', rsRoiHtml(fresh)); rsRedraw(root, 'rs-costs', rsCostsHtml(fresh));
      const m2 = root.querySelector('#' + msgId); if(m2){ m2.className = 'small ok'; m2.textContent = 'Saved.'; }
    } catch(e){ if(msg){ msg.className = 'small sev-high'; msg.textContent = e.message; } }
  };
  const sm = v.querySelector('#rsSaveMin'); if(sm) sm.onclick = () => saveVals('minutes', '.rs-min', 'rsMinMsg');
  const sc = v.querySelector('#rsSaveCost'); if(sc) sc.onclick = () => saveVals('costs', '.rs-cost', 'rsCostMsg');
  // the owner's password typed here: tried once (a wrong try counts once), then kept in the page's memory like the
  // Providers tab's, and the two panels it opens are drawn again
  v.querySelectorAll('.rs-unlock').forEach(f => f.onsubmit = async e => {
    e.preventDefault();
    const inp = f.querySelector('input'), msg = f.querySelector('.rs-unlock-msg'), key = inp.value;
    if(!key) return;
    if(/[^\x20-\x7e]/.test(key)){ msg.textContent = "That is not the owner's password."; return; }
    state.ownerKey = key;
    try {
      await api('/api/activity/unlock');
      if(typeof pvKeyUi === 'function') pvKeyUi();
      if(typeof pvArmIdle === 'function') pvArmIdle();
      const fresh = await rsLoad(); state.rsData = fresh;
      rsRedraw(root, 'rs-hours', rsHoursHtml(fresh)); rsRedraw(root, 'rs-costs', rsCostsHtml(fresh));
    } catch(err){ if(typeof pvForget === 'function') pvForget(); else state.ownerKey = ''; msg.textContent = err.message; }
  });
  const cl = v.querySelector('#rsClin'); if(cl) cl.onclick = async e => { e.preventDefault();
    const box = v.querySelector('#rsClinBox');
    try {
      const r = await fetch('/api/results/denials/clinicians', {headers: rsHeaders()}); const j = await r.json();
      if(!r.ok) throw new Error(j.detail || 'Refused.');
      box.innerHTML = `<div class="scroll"><table><thead><tr><th>Clinician</th><th class="num">Lines</th><th class="num">Denied</th><th class="num">Dollars denied</th></tr></thead><tbody>${(j.clinicians || []).map(c => `<tr><td>${esc(c.name || c.npi || 'Unknown')}</td><td class="num">${rsNum(c.answered)}</td><td class="num">${rsPct(c.line_rate)}</td><td class="num">${rsPct(c.dollar_rate)}</td></tr>`).join('')}</tbody></table></div>`;
    } catch(err){ box.innerHTML = `<div class="note">${esc(err.message)}</div>`; }
  };
}
