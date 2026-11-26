// RemitWatch daily screens: Today (what to do now), Bigger wins (money fixed once, collected on every future claim), More
// (the analysis pages), the first-visit guide and the What to do box of an item. The category pages and the Worklist are in
// desk.js. Loaded after features.js and before the page's own script; uses its helpers (api, esc, money, money2, state,
// render, openDetail, setFindingStatus).

const KIND_STYLE = {fix:'k-fix', auth:'k-auth', note:'k-note', rule:'k-rule', rate:'k-rate', chase:'k-chase', check:'k-check', bill:'k-bill', post:'k-post'};
const STATUS_WORDS = {open:'To do', in_progress:'Started', appealed:'Sent', resolved:'Paid', lost:'Lost', dismissed:'Let go'};
const OWNER_OF = {fix:'Billing', auth:'Front desk', note:'Clinicians and coder', rule:'Billing', rate:'Billing', check:'Front desk and billing', bill:'Billing', post:'Payment posting'};
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
// today as the site counts it: the brief's date, the practice's day (a window stays open until midnight where the practice
// is), so the page agrees with the lists on which windows are still open. Before the brief is read: the reader's own day.
function localDay(){ const n = new Date(); return `${n.getFullYear()}-${String(n.getMonth()+1).padStart(2,'0')}-${String(n.getDate()).padStart(2,'0')}`; }
function siteDay(){ return (state.brief && state.brief.today) || localDay(); }
function daysUntil(d){ if(!d) return null; const t = new Date(d+'T00:00:00Z'), n = new Date(siteDay()+'T00:00:00Z'); return Math.round((t-n)/86400000); }
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
    <div class="guide-more">The tabs after Today (<b>Denials</b>, <b>Bundling & modifiers</b>, <b>Downcoding</b>, <b>Contracts & underpayment</b>, <b>Fees & takebacks</b>) hold the same work by category; the red number on a tab is what closes there within 7 days. <b>Worklist</b> has every claim item in one list, soonest first. <b>Bigger wins</b> is money you fix once and collect on every future claim: contracts, your fees, payment fees and denial causes. <b>Data</b> tells you when the numbers cannot be trusted yet.</div>
    <div class="actions"><button class="primary" id="guideGo">Show me the first item</button><button id="guideOk">Got it</button></div>
  </div>`;
}
function bindGuide(v){
  const g = v.querySelector('#guide'); if(!g) return;
  const close = () => { guideDone(); g.remove(); };
  g.querySelector('#guideClose').onclick = close;
  g.querySelector('#guideOk').onclick = close;
  // the first item to work: in Work now (never one that waits on the payer or a question for the owner), soonest first
  g.querySelector('#guideGo').onclick = () => { guideDone(); goWork({state:'todo', deadline:'valid', sort:'deadline', first:'1'}); };
}
function openGuide(){ guideReset(); if(state.tab !== 'today'){ state.tab = 'today'; location.hash = 'today'; } render(); }

// ---------------------------------------------------------------- Today
async function todayTab(v){
  // The plan comes from the brief and is drawn as soon as the brief answers. EMA's counts by state (/api/ema/today), the
  // money scoreboard (/api/results/scoreboard) and the five EMA exports' ages (/api/ema/exports) each fill their own
  // place when their answer comes: a slow one never holds the plan back, a failed one says so in one line, and step 1
  // never says all clear before an answer says no feed is behind
  const tdP = api('/api/ema/today').catch(()=>null), sbP = api('/api/results/scoreboard').catch(()=>null),
    exP = api('/api/ema/exports').catch(()=>null);
  const b = await brief(true);
  if(!b.ready){ v.innerHTML = '<div class="panel"><h2>Nothing analysed yet</h2><div class="note">The first analysis run has not finished. Open Data and press Sync and analyse now.</div></div>'; return; }
  const look = {blind: undefined};       // undefined: no answer yet; null: none came back; true or false: what it said
  const w = b.work.total;
  const done = b.done || {};
  // the day the lists count by (the brief's), not the browser's: the header and "closes today" agree
  const day = new Date(siteDay()+'T12:00:00Z').toLocaleDateString('en-US', {weekday:'long', month:'long', day:'numeric', timeZone:'UTC'});
  const alerts = (state.alertsNew||[]).filter(a=>!['DEADLINE','STALE_FEED','PROVIDER_TARGETED','SWING'].includes(a.kind) && a.severity!=='info').slice(0,3);   // a big change in one run has the banner above every page
  const missed = b.missed || {};
  // a phone: the scoreboard and the counts by state stay above the plan; the lines after them (what is not on today's
  // list, what else waits, the exports' ages) come right below it, so the plan's first step is on the first screen
  const narrow = tdNarrow(), side = '<div id="tdMoreSlot"></div><div id="tdEmaSlot"></div>';
  v.innerHTML = `${guideSeen() ? '' : guideHtml(b)}
    <div id="tdBlind"></div>
    <div id="tdHealth">${tdHealthHtml(b, look.blind)}</div>
    <div class="today-head"><div><div class="muted small">${esc(day)}</div><h1 class="page-title">Today's plan</h1></div>
      <div class="donebox"><span>Moved on today <b>${done.today_n||0}</b></span><span>This week <b>${pl(done.week_n, 'item')}</b>, ${money(done.week_cents||0)} sent or paid</span>${(b.recovered||{}).year_cents ? `<span>Paid back in ${esc(b.recovered.year)} <b>${money(b.recovered.year_cents)}</b></span>` : ''}</div></div>
    <div id="tdScoreSlot"></div>
    <div id="tdCountsSlot"></div>
    ${narrow ? '' : side}
    <div class="plan" id="tdPlan">${tdPlanHtml(tdSteps(b, look))}</div>
    ${narrow ? side : ''}
    <div class="panel"><div class="panel-head"><h2>Batches</h2><span class="muted small">One kind of work for one payer, so one person can clear a pile in one sitting. ${b.batches.length} batches, ${money(w.cents)} inside a filing window.</span></div>
      <div class="batches" id="batchList">${batchRows(b, b.batches.slice(0, 8))}</div>
      ${b.batches.length > 8 ? `<button class="ghost" id="allBatches">Show all ${b.batches.length} batches</button>` : ''}</div>
    ${alerts.length ? `<div class="panel"><div class="panel-head"><h2>What changed</h2><span class="muted small">Payer behaviour that moved recently.</span></div>${alerts.map(a=>`<div class="change"><span class="dot sev-${esc(a.severity)}">●</span><div>${esc(a.title)}</div><button class="ghost seeA" data-id="${esc(a.alert_id)}">Look into it</button></div>`).join('')}<a href="#alerts" class="small">All alerts</a></div>` : ''}
    <div class="panel wins-teaser"><div class="panel-head"><h2>Bigger wins</h2><span class="muted small">Fixed once, collected on every future claim.</span></div>${winsTeaser(b)}<button id="goWins">Open Bigger wins</button></div>
    ${missed.n ? `<div class="missed small muted">Missed in the last ${missed.days} days: ${Number(missed.n).toLocaleString()} items worth ${money(missed.cents)} passed their filing window without anyone working them. <a href="#" id="seeMissed">See them</a></div>` : ''}`;
  bindGuide(v);
  tdBindPlan(v, b, look);
  tdBindLinks(v.querySelector('#tdHealth'));
  bindBatches(v, b);
  const all = v.querySelector('#allBatches'); if(all) all.onclick = () => { v.querySelector('#batchList').innerHTML = batchRows(b, b.batches); bindBatches(v, b); all.remove(); };
  v.querySelectorAll('.seeA').forEach(bt=>bt.onclick=()=>{ const a = alerts.find(x=>x.alert_id===bt.dataset.id); if(a && typeof seeAlert==='function') seeAlert(a); });
  const gw = v.querySelector('#goWins'); if(gw) gw.onclick = () => { state.tab='wins'; location.hash='wins'; render(); };
  const sm = v.querySelector('#seeMissed'); if(sm) sm.onclick = e => { e.preventDefault(); goWork({deadline:'missed', status:'open', sort:'deadline'}); };
  // what the blind spot says decides step 1 and whether the health banner repeats a stale feed
  const seen = blind => { look.blind = blind;
    const h = v.querySelector('#tdHealth'); if(h){ h.innerHTML = tdHealthHtml(b, blind); tdBindLinks(h); }
    tdBindPlan(v, b, look, true); };
  await Promise.all([
    sbP.then(sb => { const el = v.querySelector('#tdScoreSlot'); if(el){ el.innerHTML = tdScoreHtml(sb); tdBindScore(el, sb); } }),
    tdP.then(async td => {
      const el = v.querySelector('#tdCountsSlot'), bb = v.querySelector('#tdBlind');
      if(td){
        if(el){ el.innerHTML = tdStatesHtml(td); tdBindCounts(el, td); }
        const more = v.querySelector('#tdMoreSlot'); if(more){ more.innerHTML = tdMoreHtml(td); tdBindCounts(more, td); }
        if(bb){ bb.innerHTML = tdBlindHtml(td.blind); tdBindLinks(bb); }
        seen(!!(td.blind && td.blind.blind));
        return;
      }
      // the counts did not come back (a store busy with a run, a cold start): the blind spot is asked on its own, as every
      // other page asks it, and nothing is ticked as clear while it is not known
      if(el) el.innerHTML = `<div class="oneline sev-medium" id="tdCountsFail">Today's EMA counts did not come back. Reload in a minute.</div>`;
      const bl = await tdBlindBanner(bb);
      seen(bl ? !!bl.blind : null);
    }),
    Promise.all([tdP, exP]).then(([td, ex]) => { const el = v.querySelector('#tdEmaSlot');
      if(el){ el.innerHTML = (td || ex) ? tdEmaLine(td, ex) : ''; tdBindLinks(el); } }),
  ]);
}

// the plan's steps from the brief. Step 1 is ticked "nothing closes this week" only once the blind spot is known to be
// clear (look.blind === false) and the self-check's line (/api/meta banner: safe mode, or what the app cannot see) is not up
function tdSteps(b, look){
  const blind = look.blind === true;
  const held = look.blind !== false || !!((state.meta || {}).banner);
  const w = b.work.total, top = b.batches[0], dec = b.decide, fu = b.follow_ups;
  const byKind7 = (b.work.types||[]).filter(t=>t.due7_n).sort((x,y)=>y.due7_cents-x.due7_cents);
  const steps = [];
  // never "all clear" while blind: what closes is what the files on hand show
  // on a phone the three kinds worth most and how many more, so Start stays on the first screen; the list has them all
  const kinds7 = tdNarrow() && byKind7.length > 3 ? byKind7.slice(0, 3) : byKind7, more7 = byKind7.length - kinds7.length;
  steps.push(w.due7_n ? {n:1, title:'Work what closes this week', line:`${pl(w.due7_n, 'item')} worth ${money(w.due7_cents)} ${w.due7_n===1?'has':'have'} a filing window that closes within 7 days.`,
      chips: kinds7.map(t=>`${kindBadge(b,t.key)} ${t.due7_n}`).join(' ') + (more7 ? ` <span class="muted small">and ${more7} more</span>` : ''), btn:`Start (${w.due7_n})`, go:{deadline:'week', sort:'deadline'}}
    : {n:1, title: blind ? 'Nothing closes this week in the payments on file' : 'Nothing closes this week', line: w.due30_n ? `${pl(w.due30_n, 'item')} worth ${money(w.due30_cents)} close within 30 days.` : 'No filing window closes in the next 30 days.', done:!held, btn: w.due30_n ? 'See the next 30 days' : '', go:{deadline:'month', sort:'deadline'}});
  // money the biller wrote off in EMA while the payer can still be asked for it: the quiet loss
  const wo = b.written_off || {};
  if(wo.n) steps.push({n:steps.length+1, title:'Work what EMA wrote off', line:`${pl(wo.n, 'item')} worth ${money(wo.cents)} ${wo.n===1?'was':'were'} written off in EMA but ${wo.n===1?'is':'are'} still inside the filing window${(wo.payers||[])[0] ? ` (most: ${esc(wo.payers[0].payer_group)}, ${money(wo.payers[0].cents)})` : ''}. Work ${wo.n===1?'it':'them'}, and reverse the write-off in EMA when the payer pays.`,
      btn:`Open them (${wo.n})`, go:{handling:'written_off', sort:'shortfall'}});
  // claims EMA shows sent and unpaid that no remittance answers: nobody is chasing them, and their filing limit runs
  const na = (b.no_answer || {}).chase || {}, rj = (b.no_answer || {}).rejected || {};
  if(na.n) steps.push({n:steps.length+1, title:'Chase claims the payer never answered', line:`${pl(na.n, 'claim')} worth ${money(na.cents)} ${na.n===1?'was':'were'} sent and never answered: look ${na.n===1?'it':'them'} up on the payer's portal or call${na.first_deadline ? `; the first one's date is ${esc(longDate(na.first_deadline))}` : ''}.${rj.n ? ` ${Number(rj.n).toLocaleString()} more never reached the payer: rejected, or still in EMA's print batch (under Fix and resubmit).` : ''}`,
      chips: kindBadge(b, 'chase'), btn:`Chase them (${na.n})`, go:{kind:'chase', sort:'deadline'}});
  if(fu && fu.n) steps.push({n:steps.length+1, title:'Follow up on what you sent', line:`${pl(fu.n, 'item')} sent to the payer more than ${fu.days} days ago ${fu.n===1?'is':'are'} still unpaid (${money(fu.cents)}). Check the portal or call.${fu.note ? ' <b>' + esc(fu.note) + '</b>' : ''}`, btn:`Follow up (${fu.n})`, go:{status:'appealed', deadline:'followup', sort:'deadline'}});
  if(top) steps.push({n:steps.length+1, title:'Clear the biggest batch', line:`${esc(kindOf(b,top.kind).label)}, ${esc(top.payer_group)}: ${pl(top.n, 'item')} on ${pl(top.claims, 'claim')}, ${money(top.cents)}${top.top_code?`, mostly ${esc(top.top_code)}`:''}. The first window ${closesText(top.first_deadline).text === 'closes today' ? 'closes today' : 'closes '+closesText(top.first_deadline).text}.`,
      chips: kindBadge(b, top.kind), btn:'Open the batch', go:{kind: top.kind, payer: top.payer_group, sort:'deadline'}});
  const older_n = Math.max(0, (b.work.undated_n||0) - (dec ? dec.n : 0)), older_c = Math.max(0, (b.work.undated_cents||0) - (dec ? dec.cents : 0));
  if(dec && dec.n){ const g = dec.groups[0]; steps.push({n:steps.length+1, title:'Decide on new items with no filing date', line:`${pl(dec.n, 'item')} worth ${money(dec.cents)} paid in the last ${dec.days||120} days${dec.since ? ` (since ${esc(longDate(dec.since))})` : ''} need a person to say what they are (bill another payer or the patient, appeal, or let go).${g?` Biggest: ${esc(labelOf(b,g.signal).label.toLowerCase())}, ${esc(g.payer_group)} (${g.n}, ${money(g.cents)}).`:''}${older_n?` <a href="#" class="see-undated">${pl(older_n, 'older one')} (${money(older_c)})</a> ${older_n===1?'has':'have'} no filing date either.`:''}`, btn:`Look at them (${dec.n})`, go:{deadline:'decide', status:'open,in_progress', sort:'shortfall'}}); }
  else if(older_n) steps.push({n:steps.length+1, title:'Items with no filing date', line:`${pl(older_n, 'item')} worth ${money(older_c)} ${older_n===1?'has':'have'} no filing date: a person has to say what they are.`, btn:'Look at them', go:{deadline:'older', status:'open,in_progress', sort:'shortfall'}});
  return steps;
}
function tdPlanHtml(steps){
  return steps.map(s=>`<div class="step ${s.done?'is-done':''}"><div class="num">${s.done?'✓':s.n}</div><div class="body"><div class="title">${esc(s.title)}</div><div class="line">${s.line}</div>${s.chips?`<div class="chips">${s.chips}</div>`:''}</div>${s.btn?`<button class="${s.done?'':'primary'}" data-step="${s.n}">${esc(s.btn)}</button>`:''}</div>`).join('');
}
function tdBindPlan(v, b, look, redraw){
  const el = v.querySelector('#tdPlan'); if(!el) return;
  const steps = tdSteps(b, look);
  if(redraw) el.innerHTML = tdPlanHtml(steps);
  el.querySelectorAll('[data-step]').forEach(bt=>bt.onclick=()=>{ const s = steps.find(x=>String(x.n)===bt.dataset.step); if(s) goWork(s.go); });
  el.querySelectorAll('.see-undated').forEach(a=>a.onclick = e => { e.preventDefault(); goWork({deadline:'older', status:'open,in_progress', sort:'shortfall'}); });
}
// the data checks that are not right: while a feed is behind, the blind-spot banner says so in dollars, so the health
// banner does not say it a second time (and holds those checks back until the blind spot is known)
function tdHealthHtml(b, blind){
  const bad = (b.health||[]).filter(h=>!h.ok && !((blind === true || blind === undefined) && TD_BLIND_KEYS.has(h.key)));
  return bad.length ? `<div class="banner"><b>Before you start:</b> ${esc(bad[0].title)}.${tdNarrow() ? '' : ' ' + esc(bad[0].detail||'')} <a href="#data" class="go-data">How to fix</a>${bad.length>1?` · <a href="#data" class="go-data">${bad.length-1} more data ${bad.length-1===1?'check':'checks'}</a>`:''}</div>` : '';
}

// ---------------------------------------------------------------- Today: the blind spot, the scoreboard, the counts by state
// The health checks the blind-spot banner already says, in dollars, while a feed is behind
const TD_BLIND_KEYS = new Set(['feed', 'ema_ar_stale', 'ema_production_stale']);
// [figure, label, one-line hint under the number, the longer words on hover]
const TD_SCORE = [['recovered_cents', 'Recovered', 'came back after flagging', 'Money that came back on items RemitWatch flagged or worked'],
  ['lost_cents', 'Lost to deadlines', 'windows closed, unworked', 'Items whose last window closed while they waited for work'],
  ['at_risk_cents', 'At risk in 14 days', 'windows closing soon', 'Items to work now whose window closes within 14 days'],
  ['given_up_cents', 'Given up without a decision', 'written off, nobody decided', 'Written off with no person deciding and no owner policy covering it']];
const TD_STATES = [['todo', 'Work now', 'to work, follow-ups due included', 'Items that need a person; the follow-ups that came due included'],
  ['followup_due', 'Follow-ups due', 'the payer had its time', "Part of Work now: work was done and the payer's usual time to answer has passed"],
  ['waiting', 'Waiting', 'on the payer', 'Sent or worked; the payer still has time to answer']];
const TD_QUIET = [['done', 'done'], ['accepted', 'accepted'], ['expired', 'expired'], ['cant_check', "can't be checked"]];
// where a link into the Data tab lands (data.js scrolls to it once its panels are drawn)
const TD_DATA = {post: 'dtPostList', refund: 'dtRefundList'};
// a phone (640 px or less): the figures in two compact lines, so the plan's first step stays on the first screen
function tdNarrow(){ return typeof window !== 'undefined' && window.innerWidth > 0 && window.innerWidth <= 640; }

// What the site cannot check while a feed (the remittances, EMA's A/R or Production report) is behind, in dollars. Any page
// may show it: tdBlindBanner(el) asks the site, or takes the answer it already has, and gives back what it showed.
function tdBlindHtml(bl){
  if(!bl || !bl.blind) return '';
  const lines = (bl.lines && bl.lines.length) ? bl.lines : [bl.basis || 'A feed is behind: what arrived since is not checked.'];
  // each part its own dollars and what they rest on; one figure only when there is one part
  const parts = (bl.parts || []).filter(p => p.cents);
  const head = parts.length > 1 ? `: ${parts.map(p => `<span title="${esc(p.basis || '')}">${money(p.cents)} ${esc(p.words || '')}</span>`).join(' and ')}`
    : (bl.cents ? `: <span title="${esc((parts[0] || {}).basis || bl.basis || '')}">${money(bl.cents)}</span> the site cannot check` : '');
  const anchor = (bl.feeds || []).some(f => f.stale && f.feed === '835') ? 'dtMissing' : 'dtReports';
  const go = `<a href="#data" class="go-data" data-anchor="${anchor}">What to do</a>`;
  // on a phone the headline keeps its dollars and the link; why folds away, so the plan stays on the first screen
  if(tdNarrow()) return `<div class="banner" role="status" style="border-left-color:var(--bad)"><b>Blind spot${head}.</b> ${go}<details style="margin-top:4px"><summary>Why</summary>${lines.map(esc).join(' ')}</details></div>`;
  return `<div class="banner" role="status" style="border-left-color:var(--bad)"><b>Blind spot${head}.</b> ${lines.map(esc).join(' ')} ${go}</div>`;
}
async function tdBlindBanner(el, given){
  if(!el) return undefined;
  let bl = given;
  if(bl === undefined){ try { bl = await api('/api/ema/blind'); } catch(e){ return undefined; } }
  el.innerHTML = tdBlindHtml(bl);
  tdBindLinks(el);
  return bl;
}
// into the Data tab, landing on what the link names; on the Data tab itself, a scroll to it (no redraw)
function tdGoData(anchor){
  if(state.tab === 'data' && anchor && typeof document !== 'undefined'){ const el = document.querySelector('#' + anchor); if(el && el.scrollIntoView){ el.scrollIntoView({block:'start'}); return; } }
  state.dataAnchor = anchor || null; state.tab = 'data'; location.hash = 'data'; render();
}
function tdBindLinks(el){
  if(!el) return;
  el.querySelectorAll('.go-data').forEach(a=>a.onclick=e=>{ if(e) e.preventDefault(); tdGoData(a.dataset.anchor || null); });
  el.querySelectorAll('.go-policy').forEach(a=>a.onclick=e=>{ if(e) e.preventDefault(); state.tab='policy'; location.hash='policy'; render(); });
}
const TD_EST = ' (an estimate)';
function tdTile(key, label, hint, x, cls, more, basis){
  const n = x && x.n != null ? x.n : null;
  const upper = x && x.upper_n ? ` Not in this total: up to ${money(x.upper_cents)} EMA wrote off on ${pl(x.upper_n, 'item')} no rate prices (the charge less what was paid).` : '';
  const title = [more || hint, basis || '', upper.trim()].filter(Boolean).join(' ');
  return `<div class="tile ${cls||''}" role="button" tabindex="0" data-td="${esc(key)}" title="${esc(title)}"><span class="t-l">${esc(label)}</span>
    <span class="t-v">${n == null ? '–' : Number(n).toLocaleString()}${n ? `<small>${short$(x.cents)} est.</small>` : ''}</span><span class="t-s">${esc(hint)}</span></div>`;
}
function tdScoreHtml(sb){
  // unit R's scoreboard: a figure the route does not give yet shows a dash, never a zero
  const s = sb || {}, basis = s.basis || {};
  if(tdNarrow()) return `<div class="oneline td-compact" id="tdScore" aria-label="The money scoreboard">${TD_SCORE.map(([k, label, hint, more]) => {
    const c = s[k], key = k.replace(/_cents$/, '');
    return `<a href="#" data-score="${esc(key)}" title="${esc(basis[key] || more)}">${esc(label)} <b>${c == null ? '–' : money(c)}</b></a>`; }).join(' · ')}</div>`;
  return `<div class="tiles" id="tdScore" aria-label="The money scoreboard">${TD_SCORE.map(([k, label, hint, more]) => {
    const c = s[k], key = k.replace(/_cents$/, '');
    return `<div class="tile ${k==='lost_cents'&&c?'t-red':''}${k==='at_risk_cents'&&c?' t-amber':''}" role="button" tabindex="0" data-score="${esc(key)}" title="${esc(basis[key] || more)}">
      <span class="t-l">${esc(label)}</span><span class="t-v">${c == null ? '–' : money(c)}</span><span class="t-s">${esc(hint)}</span></div>`; }).join('')}</div>`;
}
function tdStatesHtml(td){
  if(!td.ready) return '';
  const st = td.states || {}, aw = td.auto_written_off, un = td.unworked, bs = td.basis || {};
  const aT = ["EMA's posting rule wrote the denial off the day it posted, nobody worked it, and its safe date to appeal has not passed", bs.auto_written_off || ''].join(' ');
  const uT = `No work in EMA or here in the 21 days to ${longDate(un ? un.data_through : '')}. ${bs.unworked || ''}`;
  if(tdNarrow()){
    const one = (key, label, x) => x ? `<a href="#" data-td="${key}" title="${esc(key === 'unworked' ? uT : key === 'auto_written_off' ? aT : (bs.shortfall || ''))}">${esc(label)} <b>${Number(x.n||0).toLocaleString()}</b>${x.n ? ' ' + short$(x.cents) + ' est.' : ''}</a>` : '';
    const cells = TD_STATES.map(([k, label]) => one(k, label, st[k])).concat([one('auto_written_off', 'Auto write-offs still appealable', aw), one('unworked', 'Nobody has worked these', un)]).filter(Boolean);
    return `<div class="oneline td-compact" id="tdStates" aria-label="Items by state">${cells.join(' · ')}</div>`;
  }
  return `<div class="tiles" id="tdStates" aria-label="Items by state">
      ${TD_STATES.map(([k, label, hint, more]) => tdTile(k, label, hint, st[k], k==='followup_due' && (st[k]||{}).n ? 't-amber' : '', more, bs.shortfall)).join('')}
      ${aw ? tdTile('auto_written_off', 'Auto write-offs still appealable', "written off by EMA's rule, unworked", aw, aw.n ? 't-red' : '', aT) : ''}
      ${un ? tdTile('unworked', 'Nobody has worked these', 'of Work now, nothing done in 21 days', un, '', uT) : ''}
    </div>`;
}
// what is not on today's list, the money that is not claim work, and the patterns that wait for the owner: one line
function tdMoreHtml(td){
  if(!td.ready) return '';
  const st = td.states || {}, bs = td.basis || {};
  const quiet = TD_QUIET.filter(([k]) => (st[k]||{}).n).map(([k, word]) => `<a href="#" data-td="${k}">${Number(st[k].n).toLocaleString()} ${esc(word)}</a>`);
  const other = td.other || {}, post = other.post || {}, refund = other.refund || {}, dc = td.decide || {};
  const also = [post.n ? `<a href="#" data-td="post" title="${esc(bs.post || '')}">${esc(pl(post.n, 'payment'))} to post in EMA</a>` : '',
    refund.n ? `<a href="#" data-td="refund" title="${esc(bs.refund || '')}">${esc(pl(refund.n, 'credit'))} to refund</a>` : ''].filter(Boolean);
  // codes a payer almost never pays: out of Work now until the owner decides (PLAN decision 14), never out of sight
  const decide = dc.patterns ? `<a href="#policy" class="go-policy" title="${esc(bs.payer_policy || '')}">${esc(pl(dc.patterns, 'write-off pattern'))} worth ${money(dc.cents)} (est.) ${dc.patterns===1?'waits':'wait'} for the owner's decision</a>` : '';
  if(!quiet.length && !also.length && !decide) return '';
  return `<div class="oneline">${quiet.length ? 'Not on today\'s list: ' + quiet.join(', ') + '.' : ''}${also.length ? ' Also: ' + also.join(', ') + '.' : ''}${decide ? ' ' + decide + '.' : ''}</div>`;
}
function tdEmaLine(td, ex){
  const parts = [];
  ((ex && ex.exports) || []).forEach(e => { if(e.waiting) parts.push(`${esc(e.title)}: uploaded, waiting to be read`);
    else if(e.overdue){
      // the age is of what RemitWatch read: a file that arrived and is held or not read yet has none (needs_A2 F.5)
      const age = e.age_days != null ? esc(pl(e.age_days, 'day')) + ' old' : e.held ? 'the newest file is held' : (e.unread || e.stuck || e.arrived) ? 'none read yet' : 'never arrived';
      parts.push(`<span class="sev-high">${esc(e.title)}: ${age}</span>`); }
    else if(e.stuck) parts.push(`${esc(e.title)}: an upload has not been read yet`); });
  const rm = td && td.remits_missing;
  // the run's own judgment of last month (ema_checks data health), said in words: the month by name, the dollars
  const missing = rm && rm.ok === false ? `<a href="#data" class="go-data" data-anchor="dtMissing">${rm.month && rm.cents != null
    ? `EMA posted ${money(rm.cents)} of insurance payments in ${rwMonth(rm.month)} with no remittance` : esc(rm.title || 'Payments with no remittance')}</a>` : '';
  // a report that waits says why and what to do (the catalog's own words); a held one needs a person's decision, unless
  // someone chose Apply since the newest run (then it waits for the next run)
  const names = {ar_report: 'The A/R report', production: 'A Production report'};
  const waits = ((td && td.reports_waiting) || []).map(w => `${esc(names[w.report] || 'A report')} waits: ${esc(w.reason || 'the next run reads it')}`)
    .filter((t, i, a) => a.indexOf(t) === i);
  const held = td && td.reports_held ? `<a href="#data" class="go-data" data-anchor="dtReports">${esc(pl(td.reports_held, 'EMA report'))} ${td.reports_held === 1 ? 'waits' : 'wait'} for a decision</a>` : '';
  const pending = td && td.reports_pending ? `<a href="#data" class="go-data" data-anchor="dtReports">${esc(pl(td.reports_pending, 'EMA report'))} ${td.reports_pending === 1 ? 'waits' : 'wait'} for the next run</a>` : '';
  const extra = [held, pending].concat(waits).filter(Boolean);
  if(!parts.length && !missing && !extra.length) return ex && (ex.exports||[]).length ? `<div class="oneline">EMA's exports are up to date (A/R within 8 days, Production within 14, the weekly exports within 8).</div>` : '';
  return `<div class="oneline">${parts.length ? `EMA's exports: ${parts.join(' · ')}. <a href="/upload">Upload</a>` : ''}${parts.length && missing ? ' · ' : ''}${missing}`
    + `${extra.length ? `${parts.length || missing ? ' · ' : ''}${extra.join(' · ')}` : ''}</div>`;
}
// each count opens the list it was counted with: a state opens the Worklist in the Worklist's own order (the same list
// whichever door), the payments to post and the credits to refund open their lists on the Data tab
function tdBindCounts(el, td){
  const st = (td && td.states) || {}, other = (td && td.other) || {};
  const goOf = k => (st[k] || (td||{})[k] || other[k] || {}).go;
  el.querySelectorAll('[data-td]').forEach(x => { const key = x.dataset.td, go = goOf(key); if(!go) return;
    const open = e => { if(e) e.preventDefault();
      if(TD_DATA[key]){ tdGoData(TD_DATA[key]); return; }
      goWork(Object.assign({}, go)); };
    x.onclick = open; x.onkeydown = e => { if(e.key === 'Enter' || e.key === ' ') open(e); }; });
  tdBindLinks(el);
}
function tdBindScore(el, sb){
  el.querySelectorAll('[data-score]').forEach(x => { const key = x.dataset.score, o = ((sb||{}).open||{})[key];
    const open = e => { if(e) e.preventDefault();
      if(o && typeof o === 'object'){ goWork(o); return; }
      state.resultsOpen = key; const h = (typeof o === 'string' && o.startsWith('#')) ? o.slice(1) : 'results';
      state.tab = h.split('/')[0]; location.hash = h; render(); };
    x.onclick = open; x.onkeydown = e => { if(e.key === 'Enter' || e.key === ' ') open(e); }; });
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
const WL_STATUSES = [['open,in_progress','To do and started'],['open','To do'],['in_progress','Started'],['appealed','Sent'],['in_progress,appealed','Started and sent'],['resolved','Paid'],['lost','Lost'],['dismissed','Let go'],['all','Everything']];
const MONEY_CATS = 'denial,bundling,downcoding,underpayment';
// the "What to do" box at the top of an item, and stepping through the list
function whatToDoHtml(f, letter){
  const b = state.brief; if(!b || !b.labels) return '';
  const lb = labelOf(b, f.signal), kk = kindFor(b, f), k = kindOf(b, kk), est = /not confirmed/.test(f.deadline_basis||'');
  const ev = (f.evidence && typeof f.evidence === 'object') ? f.evidence : {}, today = siteDay();
  const open = (ev.deadlines||[]).filter(x=>Array.isArray(x) && x[0] && x[0] >= today).map(x=>x[0]).sort();
  const due = open[0] || f.appeal_deadline, c = closesText(due);
  const next = f.next_step || ((f.signal === 'CLAIM_DENIED_UNPAID' && kk !== 'check') ? k.how : lb.next);
  const openCents = (ev.deadlines||[]).filter(x=>Array.isArray(x) && x[0] && x[0] >= today).reduce((s,x)=>s+(x[2]||0), 0);
  const stake = (ev.deadlines||[]).length ? `${money2(openCents)} at stake on the claims still inside their windows (of ${money2(f.shortfall_cents)} in all).`
    : (f.shortfall_cents ? money2(f.shortfall_cents)+' at stake.' : 'The amount is not known: this payer has no usual rate for the code to compare with.');
  const ids = state.worklist||[], i = ids.indexOf(f.finding_id);
  const more = i>=0 && (i < ids.length-1 || (!!state.wlNext && (state.wlTotal||0) > ids.length)), decide = kk === 'check' || !due;
  const waiting = {appealed: f.faxed_at ? `Faxed on ${longDate(String(f.faxed_at).slice(0,10))}. It waits on the payer: once the payer's time to answer has passed, the follow-ups are faxed by themselves every week until it is paid or decided (Faxes page).`
      : `Sent${f.appealed_at?' on '+longDate(String(f.appealed_at).slice(0,10)):''}. It waits on the payer: if it is still unpaid 30 days after sending, follow up by portal or phone.`,
    resolved:'Paid. Nothing more to do.', lost:'Lost: the payer upheld its decision.', dismissed:'Let go.'}[f.status];
  const shut = !waiting && !!due && due < today;        // a closed window: nothing to send, so no send button and no letter
  // an item already sent, paid or let go: its filing date is history, not an alarm
  const head = waiting ? `<span class="muted">${esc(STATUS_WORDS[f.status]||f.status)}${f.status==='appealed' && f.appealed_at ? ' '+longDate(String(f.appealed_at).slice(0,10)) : ''}${due ? ` · filing date ${longDate(due)}` : ''}</span>`
    : `<span class="${c.cls}">${due ? `File by ${longDate(due)} (${esc(c.text)})${est?', estimated rule':''}${open.length>1?`; its last claim by ${longDate(f.appeal_deadline)}`:''}` : 'No filing date'}</span>`;
  return `<div class="todo-box"><div class="todo-top">${kindBadge(b, kk)}${head}
      ${i>=0 ? `<span class="muted small">Item ${i+1} of ${Number(Math.max(state.wlTotal||0, ids.length)).toLocaleString()}</span>` : ''}</div>
    <div class="todo-what">${stake}</div>
    ${f.carc_text ? `<div class="small muted">Payer's reason: ${esc(f.carc)} ${esc(f.carc_text)}${f.rarc_text?' · '+esc(f.rarc)+' '+esc(f.rarc_text):''}</div>` : ''}
    ${shut ? `<div class="todo-next">The filing window closed on ${longDate(due)}. Nothing to send: keep it for the record, or mark it Lost or Let go.</div>` : waiting ? `<div class="todo-next"><b>Status:</b> ${esc(waiting)}</div>` : `<div class="todo-next"><b>What to do:</b> ${esc(next)}</div>
    <div class="small muted">You need: ${esc(f.signal === 'REJECTED_PRE_PAYER' ? ((f.evidence||{}).not_sent ? "EMA's print batch (print and mail the claim, or send it electronically)" : "EMA and the rejection's reason (on the clearinghouse's rejection report)") : (k.needs||''))}</div>`}
    <div class="todo-err sev-high small" id="todoErr" role="alert"></div>
    <div class="actions todo-actions">${more && !waiting && !shut ? `<button class="primary" id="sentNext" data-to="appealed">Mark sent and open the next</button>` : ''}
      ${more && !waiting && !shut && decide ? `<button id="letGoNext" title="Billed to the patient or another payer, or the payer was right">Let go and open the next</button>` : ''}
      ${shut ? '' : (letter||'')}${i>0 ? '<button class="ghost" id="prevItem">‹ Previous</button>' : ''}${more ? '<button class="ghost" id="nextItem">Next ›</button>' : ''}</div></div>`;
}
function bindWhatToDo(f, d, seq){
  // the item's place is read at the moment of the click: the list behind may have been redrawn while the drawer was open
  // (a bulk change, another tile, another tab), and an item no longer on the list on screen ends the walk
  const pos = () => (state.worklist||[]).indexOf(f.finding_id);
  const next = async () => {
    const i = pos(); if(i < 0) return null;
    if(i < state.worklist.length - 1) return state.worklist[i+1];
    if(state.wlNext){ try { await state.wlNext(); } catch(e){ return null; } }
    return (state.worklist||[])[i+1] || null;
  };
  // the panel still shows this item (not one the person opened since, nor closed): then, and only then, it moves on
  const here = () => typeof panelShows !== 'function' || panelShows(seq, f.finding_id);
  const go = async () => { const id = await next(); if(!here()) return; if(id) openDetail(id, {walk: true}); else closeDetail(); };
  const walk = [...d.querySelectorAll('#sentNext, #letGoNext, #prevItem, #nextItem')];
  const decideAndGo = (bt, status) => async () => {
    if(bt.disabled || (typeof walkGuarded === 'function' && walkGuarded())) return;      // a second click, or a double click's second half
    const why = letGoNote(f.signal, status);          // a claim no payer answered is let go only with a note (desk.js)
    if(why === null) return;
    if(why === undefined && status === 'dismissed' && !confirm('Let go of this item? It leaves the list and counts as let go.')) return;
    walk.forEach(x => x.disabled = true);             // one decision per click: nothing else in the walk until the answer is back
    const body = why === undefined ? {status} : {status, notes: why};
    body.seen_status = f.status;                      // the status this panel shows: a change made elsewhere since is not undone
    try { await post('/api/findings/'+f.finding_id+'/status', body); }
    catch(e){
      if(e.code === 'changed' || e.code === 'in_flight') state.dirty = true;
      if(!here()){ if(typeof notice === 'function') notice('Not saved: ' + (e.message || 'no answer from the site')); return; }
      walk.forEach(x => x.disabled = false); const er = d.querySelector('#todoErr');
      if(er) er.textContent = 'Not saved: ' + (e.message || 'no answer from the site') + (e.code ? '' : ' Try again.'); return; }
    state.brief = null; state.dirty = true;
    if(here()) go(); else if(typeof notice === 'function') notice(`Saved: ${f.title || 'the item'} is now ${STATUS_WORDS[status] || status}.`); };
  const sn = d.querySelector('#sentNext'); if(sn) sn.onclick = decideAndGo(sn, sn.dataset.to || 'appealed');
  const lg = d.querySelector('#letGoNext'); if(lg) lg.onclick = decideAndGo(lg, 'dismissed');
  const pv = d.querySelector('#prevItem'); if(pv) pv.onclick = () => { if(pv.disabled) return; pv.disabled = true; const i = pos(); if(i > 0) openDetail(state.worklist[i-1]); else closeDetail(); };
  const nx = d.querySelector('#nextItem'); if(nx) nx.onclick = () => { if(nx.disabled || (typeof walkGuarded === 'function' && walkGuarded())) return; nx.disabled = true; go(); };
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
    <div class="panel" id="wins-templates"><h2>Suggested templates for next time</h2><div class="muted small">Loading…</div></div>
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
  if(typeof lessonsPanel === 'function') lessonsPanel(v.querySelector('#wins-templates'));
}

// ---------------------------------------------------------------- More: the analysis pages
const MORE_PAGES = [
  ['overview','Overview','Every finding by category, month and payer, including money past its filing window.'],
  ['explorer','Who & what','Count any finding by payer, clinician, code, plan or month.'],
  ['payers','Payers & rates','Findings and dollars by payer and by clinician.'],
  ['alerts','Alerts','Payers behaving differently from their own past, new and acknowledged.'],
  ['progress','Progress','What came back after a fix or appeal, and what is being worked.', 'mid-only'],
  ['data','Data','Whether the numbers can be trusted, and who fixes what.', 'mid-only'],
  ['policy','Write-off policy','Denials written off in EMA, by payer, code and reason: pursue or accept each pattern.'],
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
