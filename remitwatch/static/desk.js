// RemitWatch category pages. Denials, Bundling & modifiers, Downcoding, Contracts & underpayment and the Worklist share one
// template: a title with one Start button, tiles that filter the list, what to do by kind of work, at most two fix-once
// notes, a 20-row list and one Details fold. Fees & takebacks has its own tiles, since nothing there has a filing window.
// Every number you can click opens a list whose count line shows that same number (/api/desk counts through the list's
// own filter). Loaded after today.js; uses its helpers and index.html's.

// Never hidden for a payer (actions.NEVER_HIDDEN, which the server enforces): a claim no payer answered or that never got
// to one, and money nobody has asked for yet or that is owed back (a payment EMA never posted, a second payer never billed,
// a credit). Each is let go one at a time.
const NEVER_HIDDEN = new Set(['NO_ANSWER', 'REJECTED_PRE_PAYER', 'NOT_SENT', 'REMIT_NOT_POSTED', 'SECONDARY_NOT_BILLED', 'CREDIT_BALANCE']);
// One at a time, and with a note. The list's Let go leaves these out and says so (bindBulk). Let go or Lost in an item's own
// panel asks why and sends the answer as its note (letGoNote); so does every other Let go and Lost, one item or many.
const ONE_AT_A_TIME = NEVER_HIDDEN;
const CLAIM_CHASES = new Set(['NO_ANSWER', 'REJECTED_PRE_PAYER']);         // claims no payer answered, in the words below
const ONE_WHY = {NOT_SENT: 'EMA shows this claim never went out', REMIT_NOT_POSTED: 'The payer paid this and EMA has not posted it',
  SECONDARY_NOT_BILLED: 'The second payer was never billed for this', CREDIT_BALANCE: 'This is money owed back (a credit)'};
const leftOutLine = (n, kinds) => (kinds || []).every(k => CLAIM_CHASES.has(k))
  ? `Let go leaves out ${pl(n, 'claim')} with no answer from the payer or rejected before the payer. Open ${n===1?'it':'each one'} to let it go, with a note.`
  : `Let go leaves out ${pl(n, 'item')} that ${n===1?'is':'are'} let go one at a time (no answer from the payer, never sent, a payment not posted, a second payer never billed, or a credit). Open ${n===1?'it':'each one'} to let it go, with a note.`;
// The reason for letting an item go, or marking it lost: money is let go only by a person with a reason (or the owner's
// policy table), and the server holds to it. Returns the reason; null when the person gave none (nothing is changed then);
// undefined for any other status, which needs none. Asking for it is the confirmation.
function letGoNote(signal, status){
  if(!['dismissed','lost'].includes(status)) return undefined;
  const claim = CLAIM_CHASES.has(signal), one = ONE_AT_A_TIME.has(signal), lost = status === 'lost';
  const typed = prompt(claim
    ? `${lost ? 'Mark this claim lost' : 'Let go of this claim'}?\n\nNo answer from a payer is on file for it, so this money may not have been asked for yet. It leaves the list${lost ? '' : ' and counts as let go'}.\n\nWhy (kept in the item's history):`
    : one ? `${lost ? 'Mark this item lost' : 'Let go of this item'}?\n\n${ONE_WHY[signal] || 'Nobody has asked for this money yet'}, so it is let go only by a person, with a note. It leaves the list${lost ? '' : ' and counts as let go'}.\n\nWhy (kept in the item's history):`
    : `${lost ? 'Mark this item lost' : 'Let go of this item'}? It leaves the list${lost ? '' : ' and counts as let go'}.\n\nWhy (kept in the item's history; required):`);
  if(typed === null) return null;
  const note = String(typed).trim();
  if(!note){ alert(claim ? 'Not changed. A claim with no answer from a payer needs a note that says why.' : one ? 'Not changed. This item needs a note that says why.'
    : 'Not changed. Letting an item go, or marking it lost, needs a reason.'); return null; }
  return note;
}
const DESKS = {
  denials:    {cat:'denial', title:'Denials', intro:'Lines and whole claims the payer paid $0 and has not paid since. Also claims the payer never answered, and claims rejected before they reached it or still in EMA\'s print batch.'},
  bundling:   {cat:'bundling', title:'Bundling & modifiers', intro:'Visits, procedures and add-ons paid $0 because the payer folded them into another service.'},
  downcoding: {cat:'downcoding', title:'Downcoding', intro:'Visits paid at a lower level than billed, and procedure codes the payer changed.'},
  contracts:  {cat:'underpayment', title:'Contracts & underpayment', intro:"Claims paid below the payer's own usual rate, and contract rates that sit below Medicare."},
  worklist:   {cat: MONEY_CATS, title:'Worklist', intro:'Every item from the four claim pages in one list, what is worth most first.'},
};
const BADGE_CAT = {denials:'denial', bundling:'bundling', downcoding:'downcoding', contracts:'underpayment'};
// The tiles go by the item's state (remitwatch.states): what to work now (with the follow-ups whose day has come), what of
// it closes within 7 days, what has no filing date and needs a person to say what it is, and what waits on the payer. The
// items that need no work (done, accepted, expired, can't be checked) are one click away under the tiles, never gone.
const DESK_TILES = [
  ['todo',      'Work now',         {state:'todo', status:'all', deadline:''}],
  ['todo_week', 'Closes in 7 days', {state:'todo', status:'all', deadline:'week'}],
  ['followup_due','Follow-ups due', {state:'followup_due', status:'all', deadline:''}],
  ['todo_decide','To decide',       {state:'todo', status:'all', deadline:'decide'}],
  ['waiting',   'Waiting',          {state:'waiting', status:'all', deadline:''}],
];
const TILE_TIPS = {todo:'Needs a person: the work now and the follow-ups whose day has come', todo_week:'Closes within 7 days: our target, or the payer\'s own limit once our target passed',
  followup_due:'Sent, and the day to follow up has come: grouped by payer, one call or portal session each', todo_decide:'No filing date: a person says what the item is',
  waiting:'Sent and waiting on the payer until the follow-up day'};
// the orders a list can take: the money ranking says what it ranks by (CRITIQUE_biller change 6)
const ORDERS = [['value','Most likely money first'],['shortfall','Biggest amount'],['deadline','Due soonest'],['payer_money','By payer, most money first'],['payer','By payer name']];
// the chance marks under an amount: the payer's recovery rate for the pattern below or above these moves the row
const LOW_CHANCE = 0.35, HIGH_CHANCE = 0.75;
// the owner's work: a rate pattern is a contract question, not a claim to work (detect.PATTERN_SIGNALS)
const OWNER_SIGNALS = new Set(['SECOND_RATE', 'OLD_FEE_SCHEDULE']);
// the views a biller works one payer at a time, on the payer's portal or the phone (CRITIQUE_biller change 8)
function byPayerView(f){ return f.kind === 'chase' || f.signal === 'NO_ANSWER' || f.state === 'followup_due' || f.deadline === 'followup'; }
const DESK_QUIET = [['done','Done','paid or settled, with proof'], ['accepted','Accepted','let go, lost or the owner\'s policy'],
  ['st_expired','Expired','past every window'], ['cant_check','Can\'t be checked','the money reaches no source we read']];
const QUIET_STATE = {done:'done', accepted:'accepted', st_expired:'expired', cant_check:'cant_check'};
const WL_STATES = [['todo','Work now and follow-ups due'],['work_now','Work now'],['followup_due','Follow-ups due'],['waiting','Waiting'],['done','Done'],
  ['accepted','Accepted'],['expired','Expired'],['cant_check','Can\'t be checked'],['','Any state']];
const WL_EMA = [['','Anything EMA shows'],['auto_written_off','EMA wrote it off when the denial posted'],['written_off','Written off in EMA'],
  ['contractual','Written off as contractual'],['paid_other','Paid by another payer in EMA'],['on_ar','On EMA\'s A/R'],['ema_only','Only in EMA\'s reports'],
  ['worked','Worked in EMA'],['unworked','Nobody has worked these']];
// the instruction for each kind of work (the brief carries the same words; these cover an older server)
const KIND_SHORT = {fix:'Correct it in EMA and resend it the way the item says.', auth:'Find the authorization and appeal, or ask for a retro-auth.',
  note:'Send the appeal letter with the visit note from EMA.', rule:'Send the appeal letter; it cites the coding rule.',
  rate:'Ask the payer to reprocess at its usual rate.', check:'Decide: other payer, the patient, appeal, or let go.'};
const ROWS = 20;
const DESK_DEFAULTS = {kind:'', payer:'', status:'all', deadline:'', state:'todo', ema:'', q:'', sort:'value', signal:'', paid:'', min:'', year:'', handling:''};
const ALERT_FLOOR = 50000;          // a payer change worth less than $500 takes no line on a category page
const CHANGE_WORDS = {RAMP:'ramping up', RISING:'climbing', RESUMED:'back again'};
const BULK_WORDS = {in_progress:'Mark started', appealed:'Mark sent', dismissed:'Let go', open:'Back to To do'};

const short$ = c => { const d = Math.abs(c||0)/100; return (c<0?'-':'') + (d >= 100000 ? '$'+Math.round(d/1000)+'K' : '$'+Math.round(d).toLocaleString()); };
const num = n => Number(n||0).toLocaleString();
function shortOf(b, sig){ const l = labelOf(b, sig); return l.short || l.label; }
function kindLine(b, k){ return kindOf(b, k).short || KIND_SHORT[k] || ''; }
function foldIsOpen(key){ try { return localStorage.getItem('rw:fold:'+key) === '1'; } catch(e){ return false; } }
function foldRemember(key, open){ try { localStorage.setItem('rw:fold:'+key, open ? '1' : '0'); } catch(e){} }
// the service years a list can be filtered by: the years the site holds (the summary's), else this year back to 2023; a
// year chosen elsewhere (a link) is kept so the picker shows it
function serviceYears(b, cur){
  const held = ((state.summary || {}).years || []).map(String);
  const now = Number(String((b && b.today) || new Date().toISOString()).slice(0, 4)) || 2026;
  const ys = held.length ? held.slice().sort().reverse() : Array.from({length: Math.max(1, now - 2022)}, (_, i) => String(now - i));
  return cur && !ys.includes(String(cur)) ? [String(cur)].concat(ys) : ys;
}
function tileOf(f){ return DESK_TILES.find(([k, l, s]) => s.deadline === (f.deadline||'') && s.status === f.status && s.state === (f.state||'')); }
function viewName(f){
  const d = (WL_DEADLINES.find(x=>x[0]===(f.deadline||''))||['',''])[1], s = (WL_STATUSES.find(x=>x[0]===f.status)||['',f.status])[1];
  const st = f.state ? (WL_STATES.find(x=>x[0]===f.state)||['',f.state])[1] : '';
  return [st, d, ['open,in_progress','all'].includes(f.status) ? '' : s].filter(Boolean).join(' · ');
}
// a desk's filter: the defaults, then what was chosen. An older link that names a status or a filing date and no state
// (Today's steps, the guide, the red number on a tab, Bigger wins) lists by those. One that asks for the work closing soon
// (To do, or To do and started, closing within 7 or 30 days: Today's step 1 and the red number on a tab) lists the work
// now and the follow-ups due among them, never an item that only waits on the payer, and its number is counted the same
// way (/api/brief). Any other keeps every state, as the number beside it was counted.
const TODO_LINK = new Set(['open', 'open,in_progress', 'in_progress']);
const SOON_LINK = new Set(['week', 'month']);
function deskFilter(key){
  const given = state.filt[key] || {}, f = Object.assign({}, DESK_DEFAULTS, given);
  if(!('state' in given) && ('status' in given || 'deadline' in given)){
    f.state = TODO_LINK.has(f.status) && SOON_LINK.has(f.deadline || '') ? 'todo' : '';
    if(!('sort' in given)) f.sort = 'deadline';
  }
  if(!('sort' in given) && byPayerView(f)) f.sort = 'payer_money';
  return f;
}
// one line while the payment feed is behind: an item on the list may already be paid. The day is how far the remittances
// reach for most payers (the Data tab's feed check), not the newest payment of any one: one stray file does not move it.
// Left out while the blind-spot bar under the tabs says it, in dollars (index.html blindBar): the same news once
function dataLine(b){
  if(state.blind && state.blind.blind) return '';
  const h = (b.health||[]).find(x=>!x.ok && x.key==='feed');
  return h ? `<div class="oneline"><span class="sev-medium">●</span> Payments after ${esc(longDate((h.feed||b.feed_date||b.newest_payment||'').slice(0,10)))} are not all in yet; some items may already be paid. <a href="#data" class="go-data">Data</a></div>` : '';
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
  const D = DESKS[key];
  // the brief (labels, kinds of work) and the page's own answers are asked together: the first rows wait on none of them
  // in turn. The brief the page's first load already asked for is reused (state.briefP), never asked twice
  const briefNow = (state.briefP || Promise.resolve(null)).then(x => x || brief());
  const f = deskFilter(key);
  state.lim = state.lim || {}; const lim = state.lim[key] || ROWS;
  const typed = String(f.q||'').slice(0,200);
  let sent = ''; try { sent = encodeURIComponent(typed); } catch(e){ sent = encodeURIComponent(typed.replace(/[\uD800-\uDFFF]/g,'')); }
  const find = Object.assign({cache:'no-store'}, sent ? {headers:{'X-Search':sent}} : {});
  const scope = {category:D.cat, live:1, payer:f.payer, status:f.status, deadline:f.deadline, signal:f.signal, paid:f.paid, min:f.min, year:f.year, handling:f.handling, state:f.state, ema:f.ema};
  // the Worklist lists the owner's rate patterns after a biller's claims, counted and findable (CRITIQUE_biller change 19)
  const listUrl = n => '/api/findings?'+qs(Object.assign({}, scope, {kind:f.kind, sort:f.sort, limit:n, offset:0, owner_last: key === 'worklist' ? 1 : ''}));
  // the tiles count under the chosen kind of work and reason; the kinds of work under the chosen tile, payment window and search
  const [b, dk, res, why, payers] = await Promise.all([briefNow,
    api('/api/desk?'+qs({category:D.cat, payer:f.payer, min:f.min, status:f.status, deadline:f.deadline, paid:f.paid, kind:f.kind, reason:f.signal, year:f.year, handling:f.handling, state:f.state, ema:f.ema, tileset:'state'}), find),
    api(listUrl(lim), find),
    f.kind ? api('/api/worklist/counts?'+qs(Object.assign({}, scope, {signal:'', kind:f.kind})), find) : Promise.resolve(null),
    payerList()]);
  if(run !== state.run) return;
  if(!b || !b.ready){ v.innerHTML = '<div class="panel"><h2>Nothing analysed yet</h2><div class="note">The first analysis run has not finished. Open Data and press Sync and analyse now.</div></div>'; return; }
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
  const closed = ['expired','missed'].includes(f.deadline) || ['done','accepted','expired','cant_check'].includes(f.state);
  const waiting = f.status === 'appealed' || f.state === 'waiting';
  // the segments: the page's one header row with the view's name (DESIGN P2; CRITIQUE_premium change 3). Each is a count
  // that opens its own list (/api/desk counts through the list's own filter); red only on "Closes in 7 days", only when it
  // holds something. Follow-ups due and To decide show when they hold something, or when they are the list shown
  const segs = DESK_TILES.filter(([k]) => ['todo','todo_week','waiting'].includes(k) || t(k).n || onTile(k)).map(([k, label]) => {
    const x = t(k), on = onTile(k), red = k === 'todo_week' && x.n;
    return `<button class="seg ${on?'on':''} ${red?'is-red':''}" data-tile="${k}" aria-pressed="${on}" title="${esc(TILE_TIPS[k]||'')}">${esc(label)} <b>${k==='todo_week'&&!x.n ? 'None' : num(x.n)}</b>${x.n ? `<span class="wk-c">${short$(x.cents)}</span>` : ''}</button>`; }).join('');
  // the items that need no work, by state: findable under the list, never gone ("Not on the list", DESIGN P2)
  const quiet = DESK_QUIET.filter(([k]) => t(k).n || f.state === QUIET_STATE[k]).map(([k, label, hint]) => `<a href="#" class="see-state ${f.state===QUIET_STATE[k]?'on':''}" data-state="${QUIET_STATE[k]}" title="${esc(hint)}">${esc(label)} ${num(t(k).n)}</a>`);
  const all = t('all');
  const allTip = 'Every item to do, whatever its state or filing window (open, started, sent).';
  // what to do, one row per kind of work under the chosen segment: under the list, in a fold
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
      ${single && !f.payer ? pileRows(k) : ''}`; }).join('')
    || '<div class="muted small wk-none">Nothing here. Pick another count at the top.</div>';
  const fix = key === 'worklist' ? {} : fixOnce(key, b);
  const chip = (k, label) => `<span class="fchip">${label} <button data-clear="${k}" aria-label="Remove this filter">✕</button></span>`;
  const chips = [];
  if(f.signal) chips.push(chip('signal', esc(shortOf(b, f.signal))));
  if(f.paid) chips.push(chip('paid', `Paid in the last ${esc(f.paid)} days`));
  if(f.year) chips.push(chip('year', `Services of ${esc(f.year)}`));
  if(f.handling === 'written_off') chips.push(chip('handling', 'Written off in EMA, still workable'));
  if(f.ema) chips.push(chip('ema', esc((WL_EMA.find(x=>x[0]===f.ema)||['',f.ema])[1])));
  if(f.min) chips.push(chip('min', `Over $${esc(f.min)}`));
  if(f.q) chips.push(chip('q', `“${esc(f.q)}”`));
  // a reason this payer is known not to pay: one click takes it off the lists (and keeps new ones off)
  if(f.signal && f.payer && !closed && !waiting && !NEVER_HIDDEN.has(f.signal)) chips.push(`<button class="ghost small" id="hideRule" title="Its open items go to Let go with your note, and new ones will too. Undo it under Hidden.">Hide “${esc(shortOf(b, f.signal))}” for ${esc(f.payer)}</button>`);
  if(!tile) chips.push(`<span class="fchip">${esc(viewName(f))} <button data-reset="1" aria-label="Back to Work now">✕</button></span>`);
  const sel = (name, label, opts, cur) => `<select data-f="${name}" aria-label="${label}">${opts.map(([val,l])=>`<option value="${esc(val)}" ${String(cur)===val?'selected':''}>${esc(l)}</option>`).join('')}</select>`;
  const plist = payers.concat(f.payer && !payers.includes(f.payer) ? [f.payer] : []);
  // the service year (the owner's decision 2, its follow-up: how old a bill to a patient would be shows on its step, and
  // the lists filter by it; what to do with the older ones is the owner's call, never the page's)
  const years = serviceYears(b, f.year);
  // the never-answered claims' page (the chase kind of work, or the reason): what is sent and not due yet, and the send
  // weeks whose claims went mostly unanswered (F3a's 837 pass, routed to F5)
  const naShown = f.kind === 'chase' || f.signal === 'NO_ANSWER';
  // the money ranking says what it is (CRITIQUE_biller change 6): likely money first, the biggest amount, the date
  const orders = ORDERS.concat(waiting?[['sent','Oldest sent first']]:[]);
  const kopts = [['', 'Every kind of work'], ...kinds.map(k => [k.key, `${kindOf(b, k.key).label} · ${num(k.n)} · ${money(k.cents)}`])]
    .concat(f.kind && !kinds.some(k => k.key === f.kind) ? [[f.kind, kindOf(b, f.kind).label]] : []);
  const moreOpen = !!(state.moreF && state.moreF[key]);
  const oneKind = !!f.kind || single;
  const bulkSet = closed ? ['dismissed','open'] : waiting ? ['open','dismissed'] : ['in_progress','appealed','dismissed','open'];
  // appeals faxed in bulk only where the site allows it (REMITWATCH_BULK_APPEAL): no button that can only say it is paused
  const bulk = typeof bulkOn === 'function' && bulkOn();
  // the late payers (misuse check CH-8, the Data tab's "No remittance from ... since"): their rows carry the can't-be-checked
  // mark (CRITIQUE_biller change 13). Read from the brief's own check, so the page says what the Data tab says
  const quietPayers = {};
  ((b.health||[]).find(x => x.key === 'payer_stopped' && !x.ok) || {payers: []}).payers.forEach(p => { if(p && p.payer) quietPayers[p.payer] = p.since; });
  const grouped = f.sort === 'payer_money', groups = {};
  (res.groups || []).forEach(g => { groups[g.payer_group || ''] = g; });
  const ownerWork = res.owner_work && res.owner_work.n ? res.owner_work : null;
  const ncol = f.payer ? 5 : 6;
  state.wkRows = {}; res.items.forEach(r => { state.wkRows[r.finding_id] = r; });
  let prevPayer = null, ownerShown = false;
  const body = res.items.map(r => {
    let head = '';
    if(ownerWork && !ownerShown && OWNER_SIGNALS.has(r.signal)){ ownerShown = true;
      head = `<tr class="group-row wk-owner"><td colspan="${ncol}">For the owner: ${pl(ownerWork.n, 'rate pattern')} · ${money(ownerWork.cents)}. <span class="muted">One contract question each, for the owner under Contracts; counted here, never ranked among the claims.</span></td></tr>`; }
    else if(grouped && !OWNER_SIGNALS.has(r.signal) && (r.payer_group || '') !== prevPayer){ const g = groups[r.payer_group || ''] || {n: 0, cents: 0};
      head = `<tr class="group-row wk-group"><td colspan="${ncol}"><span class="wk-gname">${esc(r.payer_group || 'Payer not known')}</span> <span class="muted">${pl(g.n, 'claim')} · ${money(g.cents)}</span><button class="ghost small wk-copy" data-payer="${esc(r.payer_group || '')}" title="The claim number, date of service and billed amount of each ${showPhi() ? 'claim, with the patient' : 'claim'}, for one call or one portal session">Copy the list for the call</button></td></tr>`; }
    prevPayer = r.payer_group || '';
    return head + rowHtml(b, r, {oneKind, onePayer: !!f.payer, oneSignal: !!f.signal, sort: f.sort, quiet: quietPayers});
  }).join('');
  const startLbl = closed || !res.items.length ? '' : 'Start';
  v.innerHTML = `${dataLine(b)}
    <div class="wk-head"><h1 class="page-title wk-title">${esc(D.title)}</h1><div class="segs wk-segs" role="group" aria-label="Which items">${segs}</div></div>
    ${f.deadline === 'followup' && (b.follow_ups||{}).note ? `<div class="note sev-medium">${esc(b.follow_ups.note)}</div>` : ''}
    <div class="list-head wk-bar" id="listHead" tabindex="-1">${tile ? '' : `<span class="lh-count"><b>${pl(res.total, 'item')}</b> · <b>${money(res.shortfall_cents)}</b></span>`}
      <span class="lh-tools">${sel('kind','Kind of work',kopts,f.kind||'')}${sel('payer','Payer',[['','Any payer'],...plist.map(p=>[p,p])],f.payer)}${sel('sort','Order',orders,f.sort)}
      <input data-f="q" maxlength="200" aria-label="Search this list" placeholder="Search  /" title="${showPhi()?'Patient, claim number or code':'Claim number or code'}" value="${esc(f.q)}"><button class="ghost" id="moreF" aria-expanded="${moreOpen}">Filters</button><button class="ghost" id="wlCsv">Export</button></span>
      ${startLbl ? `<button class="primary wk-go" id="startFirst" title="Open the first item of this list; j and k move through it">${startLbl}</button>` : ''}</div>
    <div class="more-f" ${moreOpen?'':'hidden'}><span class="ph-only">${sel('kind','Kind of work',kopts,f.kind||'')}${sel('sort','Order',orders,f.sort)}<input data-f="q" maxlength="200" aria-label="Search this list" placeholder="Claim number or code" value="${esc(f.q)}"></span>${sel('min','Amount',[['','Any amount'],['20','Hide under $20'],['100','Hide under $100'],['500','Hide under $500']],f.min)}${sel('state','State',WL_STATES,f.state||'')}${sel('year','Service year',[['','Any service year'],...years.map(y=>[y,'Services of '+y])],f.year||'')}${sel('ema','What EMA shows',WL_EMA,f.ema||'')}${sel('deadline','Filing date',WL_DEADLINES,f.deadline||'')}${sel('status','Status',WL_STATUSES.concat(WL_STATUSES.some(x=>x[0]===f.status)?[]:[[f.status,f.status]]),f.status)}<button class="ghost" id="wlClear">Reset all filters</button></div>
    ${chips.length || f.kind ? `<div class="wk-chips">${chips.join('')}${f.kind ? `<span class="wk-how"><b>What to do:</b> ${esc(doText(f.kind))}</span>` : ''}</div>` : ''}
    ${f.kind ? reasonChips : ''}
    ${(f.kind || (single && kinds[0] ? kinds[0].key : '')) === 'note' && state.meta && (state.meta.modmed || state.meta.uploads) && !closed
      ? `<div class="ema-bar wk-ema"><button id="emaBatch">${state.meta.modmed ? 'Build packets from EMA (next 10)' : 'Build packets from the uploaded notes (next 10)'}</button><span id="emaBatchMsg" class="small muted">${state.meta.modmed ? "Reads each item's visit note from EMA and reviews it into a packet, about a minute each." : 'Reviews each visit note uploaded from EMA into a packet, about a minute each. <a href="/upload">Upload notes</a>.'}</span></div>` : ''}
    <div class="bulk" id="bulk" hidden><span id="bulkN"></span>${closed || waiting || !bulk ? '' : '<button id="bulkFax">Appeal by fax</button>'}${bulkSet.map(s=>`<button data-bulk="${s}">${BULK_WORDS[s]}</button>`).join('')}<span id="bulkLeftOut" class="small muted" role="status"></span></div>
    <div class="list-card wk-card"><table class="wl wk"><thead><tr><th class="c-sel"><input type="checkbox" id="selAll" aria-label="Select every item shown"></th><th class="c-day">Due</th><th class="c-who">Patient</th>${f.payer?'':'<th class="c-payer">Payer</th>'}<th class="c-what">What happened</th><th class="num c-num">Amount</th></tr></thead>
    <tbody>${body}</tbody></table>
    ${res.items.length ? '' : `<div class="empty">Nothing here. ${chips.length ? 'Remove a filter above, or pick' : 'Pick'} another count at the top. ${esc(D.intro)}</div>`}
    ${res.total > res.items.length ? `<div class="show-more"><button class="ghost" id="moreRows">Show ${Math.min(ROWS, res.total-res.items.length)} more</button></div>` : ''}</div>
    <div class="wk-below">
      <div class="small muted wk-quiet">${quiet.length ? `Not on the list: ${quiet.join(' · ')}. ` : ''}${key==='worklist' ? '' : `<a href="#" class="see-all" title="${esc(allTip)}">Everything to do, any window: ${num(all.n)} · ${money(all.cents)}</a> `}<span id="hiddenLine" class="small muted"></span></div>
      ${fix.line ? `<div class="wk-fix">${fix.line}</div>` : ''}${fix.side || ''}
      <details class="fold wk-kinds"><summary>What to do, by kind of work${tile ? ` (${esc(tile[1])})` : ` (${esc(viewName(f))})`}</summary><div class="todo-list">${kindRows}</div>
        ${closed || waiting || !bulk ? '' : '<div class="wk-all"><button id="fxEverything" title="Every open item a letter settles, inside its window: the visit note from EMA where needed, Claude\'s review, the letter, the fax. It asks first, with the count.">Appeal everything appealable</button></div>'}</details>
      ${naShown ? '<div id="naPanel" aria-live="polite"></div>' : ''}
      ${key==='worklist' ? '' : `<details class="fold" id="fold" ${foldIsOpen(key)?'open':''}><summary>${esc(DETAILS_SUMMARY[key]||'Details')}</summary><div id="foldBody" class="muted small">Loading…</div></details>`}
    </div>`;
  // a change redraws the page; the control that made it gets the focus back (or the list head, for a click that opens a list)
  const set = (patch, focus) => { const next = Object.assign({}, f, patch);
    // the views a biller works one payer at a time (the claims to chase, the follow-ups) group by payer unless a person
    // chose another order; leaving them goes back to the money ranking (CRITIQUE_biller change 8)
    if(!('sort' in patch) && byPayerView(next) !== byPayerView(f)) next.sort = byPayerView(next) ? 'payer_money' : (f.sort === 'payer_money' ? 'value' : next.sort);
    state.filt[key] = next; state.lim[key] = ROWS; state.refocus = focus || null; render(); };
  const keyed = (el, fn) => { el.onclick = fn; el.onkeydown = e => { if((e.key==='Enter'||e.key===' ') && e.target===el){ e.preventDefault(); fn(e); } }; };
  v.querySelectorAll('[data-tile]').forEach(el=>keyed(el, e=>{ if(e.target.closest && e.target.closest('a')) return;
    const k = el.dataset.tile, s = DESK_TILES.find(x=>x[0]===k)[2];
    set(Object.assign({paid:'', q:''}, s, {sort: k==='todo_decide' ? 'shortfall' : k==='todo_week' ? 'deadline' : k==='followup_due' ? 'payer_money' : 'value'}), `[data-tile="${k}"]`); }));
  const link = (sel, patch, focus) => v.querySelectorAll(sel).forEach(a=>a.onclick=e=>{ e.preventDefault(); e.stopPropagation(); set(Object.assign({paid:'', q:''}, patch), focus); });
  link('.see-followup', {state:'followup_due', status:'all', deadline:'', sort:'payer_money'}, '#listHead');
  link('.see-all', {state:'', deadline:'', status:'open,in_progress', sort:'deadline'}, '#listHead');
  v.querySelectorAll('.see-state').forEach(a=>a.onclick=e=>{ e.preventDefault(); e.stopPropagation(); set({paid:'', q:'', state:a.dataset.state, status:'all', deadline:'', sort:'shortfall'}, '#listHead'); });
  v.querySelectorAll('.todo-row').forEach(r=>keyed(r, e=>{ if(e.target.closest && e.target.closest('a.pile')) return;
    set({kind: f.kind===r.dataset.kind ? '' : r.dataset.kind, signal:''}, `.todo-row[data-kind="${r.dataset.kind}"]`); }));
  v.querySelectorAll('.todo-chips [data-kind]').forEach(bt=>bt.onclick=()=>set({kind: f.kind===bt.dataset.kind ? '' : bt.dataset.kind, signal:''}, `.todo-chips [data-kind="${bt.dataset.kind}"]`));
  v.querySelectorAll('[data-sig]').forEach(bt=>bt.onclick=()=>set({signal: f.signal===bt.dataset.sig ? '' : bt.dataset.sig}, `[data-sig="${bt.dataset.sig}"]`));
  v.querySelectorAll('.more-why').forEach(bt=>bt.onclick=()=>{ state.allReasons = key + '|' + f.kind; render(); });
  v.querySelectorAll('a.pile').forEach(a=>a.onclick=e=>{ e.preventDefault(); e.stopPropagation(); set({kind:a.dataset.kind, payer:a.dataset.payer, signal:''}, '#listHead'); });
  v.querySelectorAll('.pile-row').forEach(r=>keyed(r, ()=>set({kind:r.dataset.kind, payer:r.dataset.payer, signal:''}, '#listHead')));
  v.querySelectorAll('select[data-f]').forEach(el=>el.onchange=()=>{
    const patch = {[el.dataset.f]: el.value};
    if(el.dataset.f === 'kind') patch.signal = '';          // a reason belongs to its kind of work
    if(el.dataset.f === 'deadline'){            // these two lists are about one status each
      if(el.value === 'followup') patch.status = 'appealed';
      else if(el.value === 'missed') patch.status = 'open';
      else if(['followup','missed'].includes(f.deadline)) patch.status = 'open,in_progress';
    }
    // 'Oldest sent first' belongs to the sent list: leaving it goes back to soonest first
    if((patch.sort || f.sort) === 'sent' && (patch.status || f.status) !== 'appealed') patch.sort = 'deadline';
    // a state chosen with a status or filing date left from an older view: the state decides, any status
    if(el.dataset.f === 'state' && el.value && f.status === 'open,in_progress' && f.deadline === 'valid'){ patch.status = 'all'; patch.deadline = ''; }
    set(patch, `select[data-f="${el.dataset.f}"]`); });
  v.querySelectorAll('[data-clear]').forEach(bt=>bt.onclick=()=>set({[bt.dataset.clear]: ''}, '#listHead'));
  v.querySelectorAll('[data-reset]').forEach(bt=>bt.onclick=()=>set({state:'todo', deadline:'', status:'all', sort:'value'}, '#listHead'));
  v.querySelectorAll('input[data-f="q"]').forEach(q=>q.onkeydown = e => { if(e.key==='Enter') set({q: q.value}, 'input[data-f="q"]'); });
  if(state.focusQ){ const q = v.querySelector('input[data-f="q"]'); if(q) q.focus(); state.focusQ = false; }
  v.querySelector('#moreF').onclick = () => { state.moreF = Object.assign({}, state.moreF, {[key]: !moreOpen}); const m = v.querySelector('.more-f'); m.hidden = moreOpen; v.querySelector('#moreF').setAttribute('aria-expanded', String(!moreOpen)); };
  v.querySelector('#wlClear').onclick = () => { state.filt[key] = {}; state.lim[key] = ROWS; render(); };
  v.querySelectorAll('.go-data').forEach(a=>a.onclick=e=>{ e.preventDefault(); state.tab='data'; location.hash='data'; render(); });
  v.querySelectorAll('.go-wins').forEach(a=>a.onclick=e=>{ e.preventDefault(); goWins(a.dataset.anchor); });
  v.querySelectorAll('.seeA').forEach(a=>a.onclick=e=>{ e.preventDefault(); const x = (state.alertsNew||[]).find(y=>y.alert_id===a.dataset.id); if(x && typeof seeAlert==='function') seeAlert(x); });
  const st = v.querySelector('#startFirst'); if(st) st.onclick = () => { if(res.items.length) openDetail(res.items[0].finding_id); };
  const eb = v.querySelector('#emaBatch'); if(eb) eb.onclick = () => emaBatch(eb, v.querySelector('#emaBatchMsg'));
  const fe = v.querySelector('#fxEverything'); if(fe) fe.onclick = () => appealEverything({payer: f.payer || '', after: render});
  const hr = v.querySelector('#hideRule'); if(hr) hr.onclick = () => hideReason(f.payer, f.signal, shortOf(b, f.signal), () => set({signal: ''}, '#listHead'));
  hiddenLine(v.querySelector('#hiddenLine'));
  const mr = v.querySelector('#moreRows'); if(mr) mr.onclick = () => { const y = window.scrollY; state.lim[key] = lim + ROWS; render().then(()=>window.scrollTo(0, y)); };
  bindRows(v);
  bindBulk(v);
  bindCopies(v);
  v.querySelector('#wlCsv').onclick = () => exportCsv(Object.assign({}, scope, {kind:f.kind, view:'worklist'}), find, key);
  const fold = v.querySelector('#fold');
  if(fold){
    const load = () => { if(fold.open && !fold.dataset.done){ fold.dataset.done = '1';
      deskDetails(key, v.querySelector('#foldBody'), b, dk, set, f, find)
        .then(()=>{ if(state.foldScroll === key){ state.foldScroll = null; (v.querySelector('#dExtra .panel') || fold).scrollIntoView({block:'start'}); } })
        .catch(e=>{ const el = v.querySelector('#foldBody'); if(el) el.textContent = 'Could not load: ' + e.message; }); } };
    fold.ontoggle = () => { foldRemember(key, fold.open); load(); }; load();
  }
  // the control that made the change gets the focus back: the visible one (a phone copy may sit hidden before it)
  if(state.refocus){ const el = [...v.querySelectorAll(state.refocus)].find(e => e.offsetParent !== null); state.refocus = null;
    if(el){ el.focus({preventScroll: el.id !== 'listHead'}); if(el.id === 'listHead') el.scrollIntoView({block:'start'}); } }
  if(f.first === '1' && res.items.length){ state.filt[key] = Object.assign({}, f, {first:''}); openDetail(res.items[0].finding_id); }
  const na = v.querySelector('#naPanel');
  if(na) api('/api/never-answered').then(d => { if(run === state.run) na.innerHTML = naPanelHtml(d); })
    .catch(e => { if(run === state.run) na.innerHTML = `<div class="note">The claims waiting for an answer could not be loaded: ${esc(e.message || String(e))}</div>`; });
}
// The never-answered page's two views (/api/never-answered): the claims sent that are not due yet, soonest due first, with
// their due days (on no list until then: blindness is loud), and the claims never answered by the week they were sent,
// newest week first, against what the 837s show sent that week, so a batch that never reached the payers stands out (the
// note names those weeks, the table marks them). Plain tables in a scroll box: they keep their headings at phone width
function naPanelHtml(d){
  if(!d) return '';
  const w = d.waiting || {}, weeks = d.weeks || [], stand = new Set(d.stand_out || []), out = [];
  if(w.n){
    const listed = Number(w.listed || 0), later = Number(w.n) - listed;
    out.push(`<div class="panel" id="naWaiting"><h2>Sent, waiting for the payer's answer</h2>
      <div class="note">${pl(w.n, 'claim')} sent (${money2(w.billed_cents)} billed) ${w.n == 1 ? 'is' : 'are'} not due yet${w.first_due ? `, due from ${longDate(w.first_due)}` : ''}${w.last_due && w.last_due !== w.first_due ? ` to ${longDate(w.last_due)}` : ''}: on no list until the due day, when a claim still unanswered is listed here to chase.</div>
      <div class="scroll na-list"><table><thead><tr><th>Claim</th><th>Payer</th><th>Sent</th><th>Due</th><th class="num">Billed</th></tr></thead>
      <tbody>${(w.claims || []).map(r => `<tr><td>${esc(r.claim)}</td><td>${esc(r.payer_group)}</td><td>${r.sent ? longDate(r.sent) : ''}</td><td>${r.due ? longDate(r.due) : ''}</td><td class="num">${money2(r.billed_cents)}</td></tr>`).join('')}</tbody></table></div>
      ${later > 0 ? `<div class="small muted">The first ${num(listed)} by due day; ${num(later)} more come due later.</div>` : ''}</div>`);
  }
  if(weeks.length){
    const usual = d.usual_share != null ? `In a usual week ${Math.round(d.usual_share * 100)}% of the claims sent get no answer. ` : '';
    const standDays = [...stand].sort().map(longDate).join(', ');
    const lead = stand.size ? `${pl(stand.size, 'week')} ${stand.size === 1 ? 'stands' : 'stand'} out (the week${stand.size === 1 ? '' : 's'} of ${standDays}): a batch that never reached the payers looks like this. Open the clearinghouse's acceptance report for ${stand.size === 1 ? 'that week' : 'those weeks'} and send again the claims the payers never received.`
      : 'No week stands out.';
    out.push(`<div class="panel" id="naWeeks"><h2>Never answered, by the week they were sent</h2>
      <div class="note">${usual}${lead}</div>
      <div class="scroll na-list"><table><thead><tr><th>Week of</th><th class="num">Sent</th><th class="num">Never answered</th><th class="num">Share</th><th>Payers</th></tr></thead>
      <tbody>${weeks.slice().reverse().map(x => `<tr${stand.has(x.week) ? ' class="na-stand"' : ''}><td>${longDate(x.week)}${stand.has(x.week) ? ' <span class="chip sev-high">stands out</span>' : ''}</td><td class="num">${x.sent == null ? '' : num(x.sent)}</td><td class="num">${num(x.unanswered)} · ${money2(x.cents)}</td><td class="num">${x.share == null ? '' : Math.round(x.share * 100) + '%'}</td><td>${(x.payers || []).map(p => `${esc(p[0])} ${num(p[1])}`).join(', ')}</td></tr>`).join('')}</tbody></table></div></div>`);
  }
  return out.join('');
}
const DETAILS_SUMMARY = {denials:'Details: by reason, by month, what each label means', bundling:'Details: by reason, by month, the 2027 changes, label meanings',
  downcoding:'Details: by reason, plans, what gets cut, by clinician', contracts:'Details: rates against Medicare by code and payer, by reason'};

function bindRows(v){
  v.querySelectorAll('tr.row[data-id]').forEach(tr=>{ tr.onclick = e => { if(e.target.closest('input, button')) return; openDetail(tr.dataset.id); };
    tr.onkeydown = e => { if((e.key==='Enter'||e.key===' ') && e.target===tr){ e.preventDefault(); openDetail(tr.dataset.id); } }; });
}
function bindBulk(v){
  const bulk = v.querySelector('#bulk'), bulkN = v.querySelector('#bulkN'); if(!bulk) return;
  const picked = () => [...v.querySelectorAll('.sel:checked')].map(x=>x.value);
  // ticked items that Let go leaves out: each is let go from its own panel, with a note (ONE_AT_A_TIME)
  const leftRows = () => [...v.querySelectorAll('.sel:checked')].map(x => x.closest('tr.row')).filter(tr => !!tr && ONE_AT_A_TIME.has(tr.dataset.signal));
  const leftOut = () => leftRows().map(tr => tr.dataset.id), leftKinds = () => leftRows().map(tr => tr.dataset.signal);
  const sync = () => { const n = picked().length, out = leftOut().length, line = v.querySelector('#bulkLeftOut'); bulk.hidden = !n; bulkN.textContent = n + ' selected';
    if(line) line.textContent = out ? leftOutLine(out, leftKinds()) : ''; };
  v.querySelectorAll('.sel').forEach(cb=>cb.onchange=sync);
  const all = v.querySelector('#selAll'); if(all) all.onchange = e => { v.querySelectorAll('.sel').forEach(cb=>cb.checked = e.target.checked); sync(); };
  const fx = v.querySelector('#bulkFax');
  if(fx) fx.onclick = () => { const rows = [...v.querySelectorAll('.sel:checked')].map(x=>x.closest('tr.row')).filter(Boolean);
    const ok = rows.filter(tr => FAX_KINDS.has(tr.dataset.kind) && !tr.dataset.closed), out = rows.length - ok.length;
    if(!ok.length){ alert('None of the ticked items is settled by an appeal letter inside its window (they are fixed in EMA, need a decision first, the payer has to be chased first, or their windows closed).'); return; }
    if(out && !confirm(`${pl(out, 'ticked item')} ${out===1?'is':'are'} not settled by an appeal letter (fixed in EMA, a decision first, the payer to chase first, or the window closed) and will be left out. Fax the other ${ok.length}?`)) return;
    if(!out && ok.length > 20 && !confirm(`Fax ${pl(ok.length, 'appeal')} now? Each takes about a minute; keep this page open.`)) return;
    appealByFax(ok.map(tr => tr.dataset.id), {after: render}); };
  v.querySelectorAll('[data-bulk]').forEach(bt=>bt.onclick=async()=>{ let ids = picked(); if(!ids.length) return;
    if(['appealed','in_progress'].includes(bt.dataset.bulk)){
      const shut = new Set([...v.querySelectorAll('tr.row[data-closed]')].map(tr=>tr.dataset.id));
      const keep = ids.filter(id=>!shut.has(id));
      if(keep.length < ids.length && !confirm(`${pl(ids.length-keep.length, 'item')} ${ids.length-keep.length===1?'has':'have'} a closed filing window and will be left out: nothing to send there.${keep.length?'':' Nothing else is ticked.'}`)) return;
      ids = keep; if(!ids.length) return;
    }
    let notes = null;
    if(bt.dataset.bulk==='dismissed'){
      const out = new Set(leftOut()), kinds = leftKinds();
      ids = ids.filter(id=>!out.has(id));
      if(!ids.length){ alert(`Nothing let go. ${leftOutLine(out.size, kinds)}`); return; }
      // one reason for all of them, kept in each item's history (the server refuses a Let go with none)
      const typed = prompt(`Let go of ${pl(ids.length, 'item')}? They leave the list and count as let go.${out.size ? ' ' + leftOutLine(out.size, kinds) : ''}\n\nWhy (kept in each item's history; required):`);
      if(typed === null) return;
      notes = String(typed).trim();
      if(!notes){ alert('Not changed. Letting items go needs a reason.'); return; }
    }
    if(bt.dataset.bulk==='appealed' && ids.length > 5 && !confirm(`Mark ${pl(ids.length, 'item')} as sent to the payer? They leave the to-do list until a follow-up is due.`)) return;
    if(bt.disabled) return;
    bt.disabled = true;
    // the status each row showed when the list was drawn: an item changed since (another tab, the daily run) is left alone
    const shown = {}; v.querySelectorAll('.sel:checked').forEach(x => { const tr = x.closest('tr.row'); if(tr && tr.dataset.status) shown[x.value] = tr.dataset.status; });
    const seen = Object.fromEntries(ids.filter(i => shown[i]).map(i => [i, shown[i]]));
    let out;
    try { out = await post('/api/findings/bulk-status', Object.assign({ids, status: bt.dataset.bulk, seen}, notes ? {notes} : {})); } catch(e){ alert('Not saved: '+e.message); bt.disabled = false; return; }
    bulkLeftAlone(out);
    state.brief = null; render(); });
}
// what a list's change left as it was, and why: items changed since the list was drawn, and items with a fax going out
function bulkLeftAlone(out){
  const ch = ((out && out.changed) || []).length, fl = ((out && out.in_flight) || []).length;
  if(!ch && !fl) return;
  alert([ch ? `${pl(ch, 'item')} changed since this list was drawn (another tab, another person or the daily run) and ${ch === 1 ? 'was' : 'were'} left as ${ch === 1 ? 'it is' : 'they are'}.` : '',
         fl ? `${pl(fl, 'item')} ${fl === 1 ? 'has' : 'have'} a fax going out now and ${fl === 1 ? 'was' : 'were'} left as ${fl === 1 ? 'it is' : 'they are'}.` : '',
         `${pl((out && out.updated) || 0, 'item')} changed. The list now shows each one as it is.`].filter(Boolean).join('\n\n'));
}
async function exportCsv(params, find, key){      // fetched, not linked: the search text travels in a header, never in the address
  let r;
  try { r = await fetch('/api/findings.csv?'+qs(params), find); } catch(e){ alert('The export did not come back (no connection).'); return; }
  if(!r.ok){ alert('The export did not come back ('+r.status+').'); return; }
  const url = URL.createObjectURL(await r.blob()), a = document.createElement('a'); a.href = url; a.download = `remitwatch-${key}.csv`; document.body.appendChild(a); a.click(); a.remove(); setTimeout(()=>URL.revokeObjectURL(url), 10000);
}
// The row a biller scans (CRITIQUE_biller change 6; DESIGN 3.8): two lines in every cell, at a fixed height, so a row never
// moves when the panel opens. Due: the verb, the date and the days left (the last day the money can be asked for: our
// target, then the payer's own limit once the target passed, change 3). Patient: the name (never blank) over the claim
// number (a click copies it) and the date of service. Payer: over the last thing a person did. What happened: the kind of
// work and the reason over the code and the payer's reason code. Amount in whole dollars over the person's status, the
// state, or the chance mark that says why the money ranking put the row there.
// the words for an item's state (remitwatch.states), the same everywhere on the site
const STATE_LABEL = {work_now:'Work now', waiting:'Waiting', done:'Done', accepted:'Accepted', expired:'Expired', cant_check:"Can't be checked"};
const AUTO_WO = {auto_written_off:'Auto write-off', written_off_while_pending:'Written off while pending', lost_to_timely_filing:'Lost to late filing'};
const DUE_VERB = {fix:'Resubmit by', auth:'Appeal by', note:'Appeal by', rule:'Appeal by', rate:'Ask by', chase:'File by', bill:'Bill by', check:'Decide by', post:'Post by'};
// "Dec 15" this year, "Dec 15, 2027" another (DESIGN 3)
function shortDay(d){
  const s = String(d || '').slice(0, 10), x = new Date(s + 'T00:00:00Z');
  if(isNaN(x)) return String(d || '');
  return x.toLocaleDateString('en-US', Object.assign({month:'short', day:'numeric', timeZone:'UTC'}, s.slice(0, 4) === String(siteDay()).slice(0, 4) ? {} : {year:'numeric'}));
}
function dueOf(b, r){
  const verb = DUE_VERB[kindFor(b, r)] || 'Due', own = r.next_deadline || r.appeal_deadline, legal = r.legal_deadline;
  const est = /not confirmed/.test(r.deadline_basis || '') ? ' (an estimated rule: confirm it in the payer agreement)' : '';
  const live = !r.status || r.status === 'open' || r.status === 'in_progress';
  const left = n => n === 0 ? 'today' : n === 1 ? 'tomorrow' : `in ${n} days`;
  if(r.work_state === 'waiting' && r.follow_up_on){ const n = daysUntil(r.follow_up_on);
    return {l1: `Follow up ${shortDay(r.follow_up_on)}`, l2: n <= 0 ? 'due now' : left(n), cls: n <= 0 ? 'due-now' : '',
            title: 'Waiting on the payer. Follow up on ' + shortDay(r.follow_up_on) + (r.state_basis ? ': ' + r.state_basis : '')}; }
  if(!live) return {l1: r.status === 'appealed' && r.appealed_at ? `Sent ${shortDay(practiceDate(r.appealed_at))}` : String(STATUS_WORDS[r.status] || r.status), l2: '', cls: 'muted', title: ''};
  if(!own) return {l1: 'No filing date', l2: '', cls: 'muted', title: 'No filing date: a person says what the item is'};
  const n = daysUntil(own), limit = legal && legal > own ? `; the payer's own limit is ${shortDay(legal)}` : '';
  if(n >= 0) return {l1: `${verb} ${shortDay(own)}`, l2: left(n), cls: n < 7 ? 'due-now' : '',
    title: `${verb} ${shortDay(own)}${est}${r.next_deadline && r.next_deadline !== r.appeal_deadline ? ' (the first of its claims)' : ''}${limit}`};
  if(legal && daysUntil(legal) >= 0){ const m = daysUntil(legal);
    return {l1: `${verb} ${shortDay(legal)}`, l2: (m === 0 ? 'last day' : `${m} day${m === 1 ? '' : 's'} left`) + ', last chance', cls: m < 7 ? 'due-now' : 'due-soon', last: true,
            title: `Last chance: our target ${shortDay(own)} passed; the payer's own limit is ${shortDay(legal)}${est}`}; }
  const shut = legal && legal > own ? legal : own;
  return {l1: `Window closed ${shortDay(shut)}`, l2: '', cls: 'muted', closed: true, title: `The last window closed on ${shortDay(shut)}${est}`};
}
function rowHtml(b, r, o){
  o = o || {};
  const lb = labelOf(b, r.signal), ws = r.work_state, due = dueOf(b, r), live = !r.status || r.status === 'open' || r.status === 'in_progress';
  const named = typeof showPhi === 'function' && showPhi();      // index.html's names switch (a page test may load desk.js alone)
  // never a blank patient cell: the name; with names on and none on file, says so beside the member ID
  const who = r.patient ? esc(r.patient) : named ? 'Name not on file' : (r.patient_control_number ? 'Claim ' + esc(r.patient_control_number) : 'Patient not shown');
  const claim = r.patient_control_number ? `<button type="button" class="clm" data-copy="${esc(r.patient_control_number)}" title="Copy the claim number">${esc(r.patient_control_number)}</button>` : '';
  const dos = r.service_date ? `seen ${shortDay(r.service_date)}` : '';
  const member = !r.patient && named && r.member_id ? `member ${esc(r.member_id)}` : '';
  // the last thing a person did, with who where a person did it (api._with_worked_by)
  const by = r.worked_by ? ` <span class="by">by ${esc(r.worked_by)}</span>` : '';
  const last = r.status === 'appealed' ? `Sent${r.appealed_at ? ' ' + shortDay(practiceDate(r.appealed_at)) : ''}${by}`
    : r.status && r.status !== 'open' ? `${esc(STATUS_WORDS[r.status] || r.status)}${r.worked_by && r.worked_at ? ' ' + shortDay(practiceDate(r.worked_at)) : ''}${by}` : '';
  // a payer whose remittances stopped (change 13): nothing on file can say whether this was paid since
  const since = (o.quiet || {})[r.payer_group];
  const blind = since ? `<span class="wk-blind" role="img" aria-label="Can't be checked" title="${esc(`Can't be checked: RemitWatch has not seen ${r.payer_group}'s payments since ${shortDay(since)}. Check the payer's portal first.`)}"></span>` : '';
  const code = `${esc(r.original_procedure_code ? r.original_procedure_code + '→' : '')}${esc(r.procedure_code || '')}${r.modifiers ? '-' + esc(r.modifiers) : ''}`;
  const carc = r.carc ? `<span title="${esc(r.carc_text || r.carc)}">${esc(r.carc)}</span>` : '';
  const auto = AUTO_WO[r.ema_outcome] ? `<span title="${esc(r.ema_outcome === 'auto_written_off' ? "EMA's posting rule wrote off the denial the day the remittance posted; nobody worked this line" : r.ema_outcome === 'lost_to_timely_filing' ? 'Written off for late filing after an earlier denial nobody fixed' : 'Written off the day it posted while the payer still asked for something')}">${esc(AUTO_WO[r.ema_outcome])}</span>` : '';
  const wo = r.written_off_cents ? `<span title="${esc(money2(r.written_off_cents))} of the money in dispute is written off (or moved to the patient) in EMA${r.written_off_on ? ', as of ' + esc(shortDay(r.written_off_on)) : ''}">written off in EMA</span>` : '';
  const what2 = [code, carc, auto, wo].filter(Boolean).join(' · ');
  const amt = r.window_cents != null ? `<b>${money(r.window_cents)}</b>` : (r.shortfall_cents ? `<b>${money(r.shortfall_cents)}</b>` : '<span class="muted" title="No usual rate to compare with">not known</span>');
  // under the amount: a person's status, the state of an item that needs no work, else the chance mark when the chance
  // moved the row in the money ranking ("low chance" or "high chance", its basis on hover)
  const quietState = ws && !['work_now','waiting'].includes(ws) ? STATE_LABEL[ws] || ws : '';
  const rate = r.recovery_rate == null ? null : Number(r.recovery_rate);
  const chance = live && o.sort === 'value' && rate != null && (rate <= LOW_CHANCE || rate >= HIGH_CHANCE)
    ? `<span class="chance" title="${esc((r.recovery_basis ? r.recovery_basis + '. ' : '') + (r.expected_value_cents != null ? `Worth about ${money(r.expected_value_cents)} of ${money(r.shortfall_cents)}.` : ''))}">${rate <= LOW_CHANCE ? 'low chance' : 'high chance'}</span>` : '';
  const under = r.window_cents != null ? `${pl(r.window_lines, 'line')} in range`
    : r.status && r.status !== 'open' ? `<span class="st st-${esc(r.status)}">${esc(STATUS_WORDS[r.status]||r.status)}</span>`
    : quietState ? `<span class="st st-${esc(ws)}" title="${esc(r.state_basis||'')}">${esc(quietState)}</span>` : chance;
  const owner = OWNER_SIGNALS.has(r.signal);
  return `<tr class="row" tabindex="0" data-id="${esc(r.finding_id)}" data-status="${esc(r.status||'open')}" data-kind="${esc(kindFor(b, r))}" data-signal="${esc(r.signal)}" ${due.closed && live ? 'data-closed="1"' : ''}${owner ? ' data-owner="1"' : ''}><td><input type="checkbox" class="sel" value="${esc(r.finding_id)}" aria-label="Select this item"></td>
    <td class="day" title="${esc(due.title)}"><span class="l1 ${due.cls}">${esc(due.l1)}</span><span class="l2">${esc(due.l2)}</span></td>
    <td class="who"><span class="l1">${who}</span><span class="l2">${[claim, dos, member].filter(Boolean).join(' · ')}</span></td>
    ${o.onePayer ? '' : `<td class="payer"><span class="l1">${esc(r.payer_group)}${blind}</span><span class="l2">${last}</span></td>`}
    <td class="what" title="${esc(lb.label + (r.hint ? '. ' + r.hint : ''))}"><span class="l1">${o.oneSignal ? esc(lb.label) : `${o.oneKind ? '' : kindBadge(b, kindFor(b, r))+' '}${esc(shortOf(b, r.signal))}`}</span><span class="l2">${what2}</span></td>
    <td class="num" title="${esc(amountBasis(r))}"><span class="l1">${amt}</span><span class="l2">${under}</span></td></tr>`;
}
// a click on a claim number copies it; "Copy the list for the call" copies one payer's claims shown (claim number, date of
// service, billed amount; the patient only with names on). Nothing goes to the server: the clipboard is the person's
function bindCopies(v){
  const said = (bt, ok) => { const was = bt.dataset.was || bt.textContent; bt.dataset.was = was; bt.textContent = ok ? 'Copied' : 'Not copied: select it by hand';
    setTimeout(() => { bt.textContent = was; }, 1800); };
  const put = async (bt, text) => { try { await navigator.clipboard.writeText(text); said(bt, true); } catch(e){ said(bt, false); } };
  v.querySelectorAll('button.clm').forEach(bt => bt.onclick = e => { e.stopPropagation(); put(bt, bt.dataset.copy || ''); });
  v.querySelectorAll('.wk-copy').forEach(bt => bt.onclick = e => { e.stopPropagation();
    const rows = Object.values(state.wkRows || {}).filter(r => (r.payer_group || '') === bt.dataset.payer && !OWNER_SIGNALS.has(r.signal));
    const lines = rows.map(r => [r.patient_control_number ? 'Claim ' + r.patient_control_number : '', r.service_date ? 'seen ' + shortDay(r.service_date) : '',
      r.charge_cents != null ? 'billed ' + money2(r.charge_cents) : '', showPhi() && r.patient ? r.patient : ''].filter(Boolean).join(', '));
    put(bt, `${bt.dataset.payer || 'Payer not known'}, ${pl(rows.length, 'claim')}\n` + lines.join('\n')); });
}
// where an amount comes from, on hover (every dollar figure says its basis; an estimate says so)
function amountBasis(r){
  const from = r.source === 'ema' ? "From EMA's postings (no remittance carries it)" : "From the payer's remittance (835)";
  const est = r.expected_cents != null ? ', against the payer\'s usual rate or the charge: an estimate until the payer pays' : '';
  return from + est + (r.recovery_basis ? `. Worth about ${money2(r.expected_value_cents)}: ${r.recovery_basis}` : '') + '.';
}
// At most two lines of money fixed once or payer behaviour. Never a claim, never a status button.
function fixOnce(key, b){
  const causes = b.causes||[];
  const catOf = {denials:/^(DENIED_|CLAIM_DENIED)/, bundling:/^(EM_BUNDLED|ADDON_BUNDLED|PROCEDURE_BUNDLED)/};
  const fam = {denials:['denial'], bundling:['bundling'], downcoding:['downcoding','code_change'], contracts:['underpayment','rate']}[key]||[];
  const trend = x => !x.n_prev ? 'new' : (x.cents90 > x.cents_prev*1.15 ? 'rising' : (x.cents90 < x.cents_prev*0.85 ? 'falling' : 'steady'));
  const line = (tag, txt, link) => `<div class="fixonce"><span class="fo-tag">${tag}</span> ${txt} ${link||''}</div>`;
  const dated = [], rising = [], change = [], steady = [];
  if(key==='bundling' && siteDay() < '2027-01-01') dated.push(line('Before Jan 1', 'G2211 becomes modifier B1: change the EMA charge setup. Billing.', '<a href="#wins" class="go-wins" data-anchor="wins-2027">Bigger wins</a>'));
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
  const fh = fp.history || {cents12: fp.cents12, payers12: fp.payers12, cents: fp.cents, since: fp.since};       // what vendors took, asked or not
  const older = fp.payers.length - fp.payers12;
  const startLbl = onFees ? (fp.payers.length ? 'Start with the biggest payer' : '') : (on90 && tbl.items.length ? 'Start with the newest takeback' : '');
  v.innerHTML = `<div class="page-head ph-row"><div><h1 class="page-title">Fees & takebacks</h1> <span class="muted">Money taken out of payments: vendor fees to stop once, and takebacks to check against the payer's letter.</span></div>
      ${startLbl ? `<div class="ph-go"><button class="primary" id="startFirst">${startLbl}</button></div>` : ''}</div>
    ${dataLine(b)}
    <div class="tiles">
      <div class="tile ${onFees?'on':''}" role="button" tabindex="0" data-l="fees"><span class="t-l">Payment fees, last 12 months</span><span class="t-v">${money(fh.cents12)}</span>
        <span class="t-s">${fp.from ? `${esc(rwMonth(fp.from))} to ${esc(rwMonth(fp.to))}, ${pl(fh.payers12, 'payer')}` : ''}${fh.since ? `; ${money(fh.cents)} since ${esc(rwMonth(fh.since))}` : ''}</span></div>
      <div class="tile ${tb90.total?'t-amber':'t-ok'} ${on90?'on':''}" role="button" tabindex="0" data-l="takebacks" data-paid="90"><span class="t-l">Taken back, last 90 days</span><span class="t-v">${tb90.total ? num(tb90.total) : 'None'}<small>${tb90.total ? short$(tb90.shortfall_cents) : ''}</small></span><span class="t-s">check each one</span></div>
      <div class="tile t-grey ${onAll?'on':''}" role="button" tabindex="0" data-l="takebacks" data-paid=""><span class="t-l">All takebacks</span><span class="t-v">${num(tbAll.total)}<small>${tbAll.total ? short$(tbAll.shortfall_cents) : ''}</small></span><span class="t-s">${since ? 'since '+esc(rwMonth(since.slice(0,7)))+' · ' : ''}${tbBig.total ? `${num(tbBig.total)} at $5,000 or more` : 'None at $5,000 or more'}</span></div></div>
    <div class="todo-list"><div class="sec">What to do</div>
      <div class="todo-row ${onFees?'on':''}" role="button" tabindex="0" data-l="fees"><span class="fo-tag">Fix once</span><span class="tr-do">Ask each payer or its vendor for direct deposit with no fee.</span><span class="tr-n"><b>${pl(fp.payers12, 'payer')}</b> to ask · ${money(fp.cents12)} in 12 months</span><span class="tr-pile small muted">${fp.payers[0] ? 'Most: '+esc(fp.payers[0].payer_name) : ''}</span></div>
      <div class="todo-row ${on90?'on':''}" role="button" tabindex="0" data-l="takebacks" data-paid="90"><span class="kind k-check">Check first</span><span class="tr-do">${tb90.total ? "Match each takeback to the payer's letter; dispute any not owed." : 'No takebacks in the last 90 days.'}</span><span class="tr-n"><b>${num(tb90.total)}</b> · ${money(tb90.shortfall_cents||0)}</span><span class="tr-pile small muted">${top ? `Most: ${esc(top.payer)} (${num(top.n)})` : ''}</span></div></div>
    ${onFees ? feeListHtml(fp, older) : `<div class="list-head" id="listHead" tabindex="-1"><span class="lh-count"><b>${pl(tbl.total, 'takeback')}</b> · <b>${money(tbl.shortfall_cents)}</b>${on90 ? ' in the last 90 days' : ', every one in the files'}</span></div>
        <div class="bulk" id="bulk" hidden><span id="bulkN"></span>${['in_progress','appealed','dismissed','open'].map(s=>`<button data-bulk="${s}">${BULK_WORDS[s]}</button>`).join('')}</div>
        <div class="scroll"><table class="wl"><thead><tr><th><input type="checkbox" id="selAll" aria-label="Select every takeback shown"></th><th>Taken</th><th>Dispute by</th><th>Payer</th><th class="num">Taken back</th></tr></thead>
        <tbody>${tbl.items.map(r=>{ const c = closesText(r.appeal_deadline), live = ['open','in_progress'].includes(r.status);
          return `<tr class="row" tabindex="0" data-id="${esc(r.finding_id)}" data-status="${esc(r.status||'open')}" ${live && r.appeal_deadline && daysUntil(r.appeal_deadline) < 0 ? 'data-closed="1"' : ''}><td><input type="checkbox" class="sel" value="${esc(r.finding_id)}" aria-label="Select this takeback"></td><td>${esc(rwMonth((r.payment_date||'').slice(0,7)))}</td><td class="day">${r.appeal_deadline ? `<span class="${live?c.cls:'muted'}" title="${esc(r.deadline_basis||'')}">${esc(c.text)}</span>` : '<span class="muted">no date</span>'}</td><td class="payer">${esc(r.payer_group)}</td><td class="num"><b>${money2(r.shortfall_cents)}</b>${r.status!=='open'?`<div><span class="st st-${esc(r.status)}">${esc(STATUS_WORDS[r.status]||r.status)}</span></div>`:''}</td></tr>`; }).join('')}</tbody></table></div>
        ${tbl.items.length ? '' : `<div class="empty">${on90 ? 'No takebacks in the last 90 days. All takebacks are in the grey box above.' : 'No takebacks.'}</div>`}
        ${tbl.total > tbl.items.length ? `<button class="ghost" id="moreRows">Show ${Math.min(ROWS, tbl.total-tbl.items.length)} more</button>` : ''}`}
    <details class="fold" id="fold" ${foldIsOpen('fees')?'open':''}><summary>Details: money held back by month, MIPS and the 2% reduction</summary><div id="foldBody" class="muted small">Loading…</div></details>`;
  const go = (l, paid) => { state.filt.feesDesk = {list: l, paid: paid || ''}; state.lim.feesDesk = ROWS; render(); };
  v.querySelectorAll('[data-l]').forEach(el=>{ const fn = () => go(el.dataset.l, el.dataset.paid != null ? el.dataset.paid : f.paid);
    el.onclick = fn; el.onkeydown = e => { if((e.key==='Enter'||e.key===' ') && e.target===el){ e.preventDefault(); fn(); } }; });
  v.querySelectorAll('.go-data').forEach(a=>a.onclick=e=>{ e.preventDefault(); state.tab='data'; location.hash='data'; render(); });
  v.querySelectorAll('button.fee-again').forEach(bt=>bt.onclick=()=>openFeeLetter(fp.asked[Number(bt.dataset.a)]));
  v.querySelectorAll('button.fee-undo').forEach(bt=>bt.onclick=async()=>{ const a = fp.asked[Number(bt.dataset.a)]; if(!a) return;
    bt.disabled = true;
    let out; try { out = await post('/api/findings/bulk-status', {ids: a.ids, status: 'open', seen: a.seen || {}}); } catch(e){ alert('Not saved: '+e.message); bt.disabled = false; return; }
    bulkLeftAlone(out);
    state.brief = null; render(); });
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
    ${asked.length ? `<h3 class="small" style="margin-top:14px">Asked, waiting for the change: ${pl(asked.length, 'payer')} · ${money(asked.reduce((s,a)=>s+(a.cents12||0),0))} in the last 12 full months</h3>
      <div class="scroll"><table class="fees"><tbody>${asked.map((a,i)=>`<tr><td>${esc(a.payer_name)}</td><td class="small muted">${a.asked ? 'asked '+esc(longDate(practiceDate(a.asked))) : ''}</td><td class="num">${money(a.cents12||0)}</td>
        <td><button class="ghost small fee-again" data-a="${i}">Letter again</button><button class="ghost small fee-undo" data-a="${i}">Not asked yet</button></td></tr>`).join('')}</tbody></table></div>` : ''}`;
}
// the request letter for one payer, and one filled button: Mark asked moves its fee months to Sent
async function openFeeLetter(p){
  if(!p) return;
  let L; try { L = await api('/api/fees/letter?'+qs({payer: p.payer_name})); } catch(e){ alert('The letter did not come back: '+e.message); return; }
  $('#modalBox').innerHTML = `<div style="display:flex;justify-content:space-between;gap:8px"><h2 style="margin:0">Payment fees: ${esc(p.payer_name)}</h2><button id="closeM" aria-label="Close">✕</button></div>
    <div class="note">${esc(L.evidence)} Send it through the payer's provider portal, by fax or by mail, then press Mark asked: its ${pl(p.ids.length, 'fee month')} move to Sent. Fees that keep arriving afterwards show here again.</div>
    <div class="actions">${p.asked ? `<span class="small muted">Asked ${esc(longDate(practiceDate(p.asked)))}.</span>` : '<button class="primary" id="feeAsked">Mark asked</button>'}<button id="copyL">Copy letter</button></div>
    <div class="st-err sev-high small" role="alert"></div>
    <pre id="letter">${esc(L.letter)}</pre>`;
  $('#modal').classList.add('open');
  $('#closeM').onclick = () => $('#modal').classList.remove('open');
  $('#copyL').onclick = () => navigator.clipboard.writeText(L.letter);
  const fa = $('#feeAsked'); if(fa) fa.onclick = async () => { const bt = $('#feeAsked'); bt.disabled = true;
    let out; try { out = await post('/api/findings/bulk-status', {ids: p.ids, status: 'appealed', seen: p.seen || {}}); }
    catch(e){ bt.disabled = false; const er = $('#modalBox .st-err'); if(er) er.textContent = 'Not saved: ' + e.message; return; }
    bulkLeftAlone(out);
    $('#modal').classList.remove('open'); state.brief = null; render(); };
  $('#closeM').focus();
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

// "Build packets from EMA": the next items that need the visit note (open, window still open, no packet yet), one at a
// time, each read from ModMed EMA and reviewed into a packet. An item whose note a person has to pick or attach (more than
// one note, only the name agrees with the claim, two patients fit, a later note) waits for a person.
async function emaBatch(btn, box){
  btn.disabled = true;
  let q;
  const byHand = !(state.meta && state.meta.modmed);          // no API yet: the notes uploaded on the Upload page
  try { q = await api('/api/appeals/ema_queue?limit=10' + (byHand ? '&ready=1' : '')); } catch(e){ box.textContent = e.message; btn.disabled = false; return; }
  if(!q.items.length){ box.innerHTML = byHand ? 'Nothing to build: no open item without a packet has an uploaded note. <a href="/upload">Upload notes</a>.'
    : 'Nothing to build: every open item that needs a note already has a packet.'; btn.disabled = false; return; }
  const n = {built: 0, skipped: 0, ambiguous: 0, not_found: 0, failed: 0};
  for(let i = 0; i < q.items.length; i++){
    box.textContent = `Building ${i + 1} of ${q.items.length} (about a minute each)…`;
    try {
      const r = await post('/api/appeals/from_ema', {finding_id: q.items[i].finding_id, skip_if_packet: true, since: q.since});
      n[r.status] = (n[r.status] || 0) + 1;
    } catch(e){
      n.failed++;
      if([502, 503].includes(e.status)){ box.textContent = `Stopped: ${e.message}`; btn.disabled = false; return; }
    }
  }
  const parts = [`${n.built} packet${n.built === 1 ? '' : 's'} built`];
  if(n.ambiguous) parts.push(`${n.ambiguous} whose note a person has to pick or attach (open the item)`);
  if(n.not_found) parts.push(byHand ? `${n.not_found} with no note uploaded` : `${n.not_found} with no signed note found in EMA`);
  if(n.skipped) parts.push(`${n.skipped} already had one`);
  if(n.failed) parts.push(`${n.failed} failed`);
  box.textContent = 'Done: ' + parts.join(', ') + '. Open an item to send its packet.';
  btn.disabled = false;
}

// ---------------------------------------------------------------- reasons hidden for a payer (the biller: "we know which payers don't pay that")
// Hiding a reason for a payer is a policy decision: first what it lets go (open items, dollars, how many still inside their
// filing window, and every future one), then a reason typed by the person, the number of items typed back, and the owner's
// password where the site has one. Nothing is filled in for them.
async function hideReason(payer, signal, label, after, item){
  let p;
  try { p = await api('/api/hide/preview?' + qs({payer_group: payer, signal})); } catch(e){ alert('Could not count them: ' + e.message); return; }
  // from an item's panel: what the archive saw happen to earlier items like this one (same payer, code and payer's reason),
  // the figure to read before hiding (/api/hide?patterns=1, archive_rules.hide_patterns)
  let seen = '';
  if(item){
    try { const pats = (await api('/api/hide?patterns=1')).patterns || [], code = item.billed_code || item.procedure_code || '';
      const q = pats.find(x => x.payer_group === payer && x.code === code && x.reason === (item.carc || ''));
      if(q && q.all) seen = `The archive saw ${pl(q.paid_later, 'earlier item')} of ${num(q.all)} like this one (same payer, code ${esc(code)} and reason ${esc(item.carc)}) paid later.`; }
    catch(e){ seen = ''; }
  }
  const box = typeof faxModal === 'function' ? faxModal(`Hide “${label}” for ${payer}?`, '') : null;
  if(!box) return;
  box.insertAdjacentHTML('beforeend', `<div class="small" style="margin:8px 0">It lets go <b>${pl(p.n, 'open item')}</b> now, worth <b>${money2(p.cents)}</b>.
      <b>${pl(p.in_window_n, 'item')}</b> of them (${money2(p.in_window_cents)}) ${p.in_window_n === 1 ? 'is' : 'are'} still inside ${p.in_window_n === 1 ? 'its' : 'their'} filing window.
      Every future item of this reason for ${esc(payer)} is let go too. Anything someone has worked stays; Show again under Hidden undoes it.
      ${seen ? `<b>${seen}</b>` : ''}
      ${p.in_flight_n ? `<b>${pl(p.in_flight_n, 'item')}</b> with a fax going out now ${p.in_flight_n === 1 ? 'is' : 'are'} left out and stay${p.in_flight_n === 1 ? 's' : ''} on the list.` : ''}</div>
    <label class="small" style="display:block;margin:6px 0">Why this payer does not pay these (shown on each item)<br><textarea id="hdNote" rows="2" maxlength="300" style="width:100%"></textarea></label>
    <label class="small" style="display:block;margin:6px 0">Type the number of items it lets go (${esc(String(p.n))}) to confirm<br><input id="hdCount" inputmode="numeric" autocomplete="off" style="width:120px"></label>
    <div class="actions"><button class="primary" id="hdGo">Hide them</button><button id="hdNo">Cancel</button></div><div id="hdMsg" class="small"></div>`);
  const msg = box.querySelector('#hdMsg'), go = box.querySelector('#hdGo');
  box.querySelector('#hdNo').onclick = () => $('#modal').classList.remove('open');
  go.onclick = async () => {
    if(go.disabled) return;
    const note = box.querySelector('#hdNote').value.trim(), typed = box.querySelector('#hdCount').value.trim();
    if(!note){ msg.className = 'small sev-high'; msg.textContent = 'Type why this payer does not pay these.'; return; }
    if(typed !== String(p.n)){ msg.className = 'small sev-high'; msg.textContent = `Type ${p.n}, the number of items it lets go, to confirm.`; return; }
    go.disabled = true;
    let r;
    try { r = await faxOwnerPost('/api/hide', {payer_group: payer, signal, note, confirm: p.n}, msg); }
    catch(e){ go.disabled = false; msg.className = 'small sev-high';
      msg.textContent = e.code === 'changed' ? `${e.message} Close this and open it again to see the new numbers.` : e.message; return; }
    $('#modal').classList.remove('open');
    state.brief = null; state.dirty = true;
    if(after) after(); else render();
    setTimeout(() => alert(`${pl(r.hidden, 'item')} hidden. New ones will be hidden too.`
      + (r.left_out ? ` ${pl(r.left_out, 'item')} with a fax going out ${r.left_out === 1 ? 'was' : 'were'} left out and stay${r.left_out === 1 ? 's' : ''} on the list.` : '')), 50);
  };
  box.querySelector('#hdNote').focus();
}
async function hiddenLine(el){
  if(!el) return;
  let h; try { h = await api('/api/hide'); } catch(e){ return; }
  const rules = h.rules || []; if(!rules.length) return;
  const n = rules.reduce((a, r) => a + (r.n || 0), 0);
  el.innerHTML = ` · <a href="#" id="hiddenMgr">Hidden: ${pl(rules.length, 'reason')} (${pl(n, 'item')})</a>`;
  el.querySelector('#hiddenMgr').onclick = e => { e.preventDefault(); hiddenManage(rules); };
}
function hiddenManage(rules){
  $('#modalBox').innerHTML = `<div style="display:flex;justify-content:space-between"><h2 style="margin:0">Hidden for a payer</h2><button id="closeM" aria-label="Close">✕</button></div>
    <div class="note">Each line keeps one reason off the lists for one payer: its items are under Let go with the note. Show again puts the ones it hid back on the lists.</div>
    <table class="small"><thead><tr><th>Payer</th><th>Reason</th><th>Why</th><th class="num">Items</th><th></th></tr></thead><tbody>
    ${rules.map((r, i)=>`<tr><td>${esc(r.payer_group)}</td><td>${esc(r.label)}</td><td class="muted">${esc(r.note||'')}<div>${esc(r.actor||'')} · ${esc(practiceDate(r.at))}</div></td><td class="num">${num(r.n)} · ${money(r.cents)}</td><td><button data-unhide="${i}">Show again</button></td></tr>`).join('')}</tbody></table>`;
  $('#modal').classList.add('open');
  $('#closeM').onclick = () => $('#modal').classList.remove('open');
  $('#modalBox').querySelectorAll('[data-unhide]').forEach(b => b.onclick = async () => {
    const r = rules[+b.dataset.unhide];
    // the rule as the list shows it, at its own grain (code, modifier, the payer's reason code): never only payer and reason
    try { await post('/api/hide', {payer_group: r.payer_group, signal: r.signal, code: r.code || '*', modifier: r.modifier || '*', reason: r.reason || '*', remove: true}); }
    catch(e){ alert('Not saved: ' + e.message); return; }
    $('#modal').classList.remove('open'); state.brief = null; state.dirty = true; render();
  });
}
// the item's panel: the same, for the reason and payer of the item open
function hideDrawerHtml(f){
  if(!f.payer_group || !f.signal || NEVER_HIDDEN.has(f.signal) || !['open','in_progress'].includes(f.status)) return '';
  const label = state.brief ? shortOf(state.brief, f.signal) : f.signal;
  return `<div class="small" style="margin:4px 0"><a href="#" id="hideFromItem" data-label="${esc(label)}">Hide “${esc(label)}” for ${esc(f.payer_group)}</a> <span class="muted">(for a reason this payer never pays)</span></div>`;
}
function bindHideDrawer(f, d){
  const a = d.querySelector('#hideFromItem'); if(!a) return;
  a.onclick = e => { e.preventDefault(); hideReason(f.payer_group, f.signal, a.dataset.label, () => { closeDetail(); render(); }, f); };
}
