// RemitWatch Results tab: the money RemitWatch brings back (split by how strongly it can claim it), lift against similar
// unworked items, cost and ROI, the work done, what became of the open book, hours saved (an estimate), denials by month and
// the daily spot checks. Every number opens the exact list of items behind it. Loaded after index.html's helpers (api, esc,
// money, money2, state, render, openDetail); every name here starts with rs or RS_, and resultsTab(v) is the page.

const RS_KEY_OF = {recovered: 'returned', lost: 'lost', at_risk: 'at_risk', given_up: 'given_up'};
const RS_NAME_KEY = 'rw:results:name';

function rsPct(x){ return x == null ? '–' : Math.round(x * 100) + '%'; }
function rsNum(n){ return Number(n || 0).toLocaleString(); }
function rsPl(n, one, many){ return `${rsNum(n)} ${Number(n) === 1 ? one : (many || one + 's')}`; }
function rsName(){ try { return localStorage.getItem(RS_NAME_KEY) || ''; } catch(e){ return ''; } }
function rsSetName(v){ try { localStorage.setItem(RS_NAME_KEY, v || ''); } catch(e){} }
function rsLink(key, text, title){ return `<a href="#" class="rs-open" data-key="${esc(key)}"${title ? ` title="${esc(title)}"` : ''}>${text}</a>`; }
function rsTile(label, value, sub, key, cls){
  return `<div class="kpi rs-tile${cls ? ' ' + cls : ''}" data-key="${esc(key || '')}" tabindex="0" role="button"><div class="l">${label}</div><div class="v">${value}</div><div class="s">${sub || ''}</div></div>`;
}
async function rsSend(path, method, body){
  const h = {'Content-Type': 'application/json', 'X-Requested-With': 'remitwatch'};
  if(state.ownerKey) h['X-Owner-Password'] = state.ownerKey;
  const r = await fetch(path, {method, headers: h, body: JSON.stringify(body || {})});
  let j = {}; try { j = await r.json(); } catch(e){}
  if(!r.ok){ const err = new Error((j && j.detail) || ('The server did not answer as expected (' + r.status + ').')); err.status = r.status; throw err; }
  return j;
}

// ---------------------------------------------------------------- the list behind a number
async function rsOpenList(key, title, page){
  page = page || 0;
  const box = $('#modalBox'); $('#modal').classList.add('open');
  box.innerHTML = '<div class="muted">loading…</div>';
  let r;
  try { r = await api('/api/results/list?' + qs({key, page, per: 50})); }
  catch(e){ box.innerHTML = `<div class="panel">${esc(e.message)}</div>`; return; }
  const pages = Math.max(1, Math.ceil(r.total / r.per));
  const rows = r.items.map(it => {
    const split = it.returned != null ? `<div class="small muted">${['a','b','c'].filter(b => it[b]).map(b => ({a:'after work', b:'found', c:'no recorded work'}[b] + ' ' + money(it[b]))).join(' · ')}${it.taken_back ? ' · taken back ' + money(it.taken_back) : ''}</div>` : '';
    const why = (it.events || []).filter(e => e.rule).slice(0, 2).map(e => `<div class="small">${esc(e.rule)}</div>`).join('');
    const where = it.listed ? `<a href="#" class="rs-item" data-id="${esc(it.finding_id)}">${esc(it.label || it.signal || 'Item')}</a>`
      : `${esc(it.label || it.signal || 'Item')} <span class="chip">no longer listed</span>`;
    return `<tr><td>${where}<div class="small muted">${esc(it.payer_group || '')}${it.procedure_code ? ' · ' + esc(it.procedure_code) : ''}${it.service_date ? ' · ' + esc(it.service_date) : ''}${it.claim_id ? ' · claim ' + esc(it.claim_id) : ''}</div>${why}</td>
      <td class="small">${esc((state.rsData && state.rsData.labels && state.rsData.labels.states[it.work_state]) || it.work_state || '')}${it.first_flagged ? `<div class="muted">flagged ${esc(it.first_flagged)}</div>` : ''}</td>
      <td class="num">${it.cents ? money(it.cents) : (it.shortfall_cents ? `<span class="muted">${money(it.shortfall_cents)}</span>` : '')}${split}</td></tr>`;
  }).join('');
  box.innerHTML = `<div class="dr-head" style="display:flex;justify-content:space-between;gap:8px;align-items:baseline"><h2 style="margin:0">${esc(title || 'Items')}</h2><button id="rsClose" aria-label="Close">✕</button></div>
    <div class="note">${rsPl(r.total, 'item')}. Open an item to see its story and why RemitWatch counts it. <a href="/api/results/list.csv?${qs({key})}">Download as CSV</a></div>
    ${r.total ? `<div class="scroll"><table><thead><tr><th>Item</th><th>State</th><th class="num">Dollars</th></tr></thead><tbody>${rows}</tbody></table></div>` : '<div class="muted">No items.</div>'}
    ${pages > 1 ? `<div class="pager"><button id="rsPrev" ${page ? '' : 'disabled'}>Previous</button><span>Page ${page + 1} of ${pages}</span><button id="rsNext" ${page + 1 < pages ? '' : 'disabled'}>Next</button></div>` : ''}`;
  box.querySelector('#rsClose').onclick = () => $('#modal').classList.remove('open');
  const prev = box.querySelector('#rsPrev'), next = box.querySelector('#rsNext');
  if(prev) prev.onclick = () => rsOpenList(key, title, page - 1);
  if(next) next.onclick = () => rsOpenList(key, title, page + 1);
  box.querySelectorAll('.rs-item').forEach(a => a.onclick = e => { e.preventDefault(); $('#modal').classList.remove('open'); openDetail(a.dataset.id); });
}

// ---------------------------------------------------------------- sections
function rsMoneyHtml(d){
  const m = d.money;
  if(!m) return `<div class="panel"><h2>Money returned</h2><div class="note">${esc(d.note || 'The figures are being worked out. Reload in a minute.')}</div></div>`;
  const any = m.anyway || {};
  const enough = any.n != null && any.n >= 20;
  const qual = !m.returned_cents ? 'Nothing has come back on flagged items yet.'
    : (any.cents != null && enough ? `About ${money(any.cents)} of this may have come back anyway: similar items with no work were paid at ${rsPct(any.rate)} (${rsPl(any.n, 'item')}).`
      : `Some of this may have come back anyway. Not enough similar items yet to say how much (${rsNum(any.n || 0)} of the 20 needed).`);
  return `<div class="panel" style="margin-top:0">
    <div class="panel-head"><h2>Money returned on claims RemitWatch flagged or worked</h2><span class="small muted">since ${esc(m.first_flag || 'the first flag')}</span></div>
    <div class="rs-top">${rsLink('returned', `<span class="rs-big">${money(m.returned_cents)}</span>`, 'Every item behind this number')}
      <span class="muted">on ${rsPl(m.items, 'item')}</span></div>
    <div class="note">${esc(qual)}</div>
    <div class="grid kpis">
      ${rsTile('After RemitWatch work', money(m.a_cents), rsPl(m.a_items, 'item') + ' paid inside the payer\'s answer time after the work', 'returned:a')}
      ${rsTile('Found by RemitWatch', money(m.b_cents), rsPl(m.b_items, 'item') + ' your own systems missed', 'returned:b')}
      ${rsTile('Flagged, no recorded work', money(m.c_cents), rsPl(m.c_items, 'item') + '; may have come back anyway', 'returned:c')}
      ${m.taken_back_cents ? rsTile('Taken back after the flag', money(m.taken_back_cents), rsPl(m.taken_back_items, 'item'), 'taken_back') : ''}
    </div>
    <details class="small" style="margin-top:8px"><summary>How this is counted</summary>
      <p>Every dollar a payer paid on an item's line after RemitWatch first flagged the item, plus money RemitWatch found. Money paid before the flag never counts. One payment counts once, on the earliest item of its line.</p>
      <p><b>After RemitWatch work</b>: paid after a fax, a claim or appeal marked Sent, a call, a payment marked posted or a bill to the patient or second payer recorded in RemitWatch, and inside that payer's usual answer time. <b>Found by RemitWatch</b>: a second payer never billed, an 835 payment never posted, a claim that never left EMA. <b>Flagged, no recorded work</b>: everything else paid after the flag. A payment the payer later took back is not counted.</p>
      ${m.median_days_work_to_money != null ? `<p>From the work to the money: ${Math.round(m.median_days_work_to_money)} days at the middle.</p>` : ''}
    </details></div>`;
}

function rsScoreHtml(s){
  if(!s) return '';
  const w = (s.weeks || []).slice(-12).reverse();
  return `<div class="panel"><h2>Week by week</h2>
    <div class="grid kpis">
      ${rsTile('Recovered', s.recovered_cents == null ? '–' : money(s.recovered_cents), s.counts.recovered == null ? 'after the first run' : rsPl(s.counts.recovered, 'item'), 'returned')}
      ${rsTile('Lost to deadlines', s.lost_cents == null ? '–' : money(s.lost_cents), s.counts.lost == null ? 'after the first run' : rsPl(s.counts.lost, 'item') + ' expired in Work now', 'lost')}
      ${rsTile('At risk in 14 days', money(s.at_risk_cents), rsPl(s.counts.at_risk, 'item') + ' in Work now', 'at_risk')}
      ${rsTile('Given up without a decision', s.given_up_cents == null ? '–' : money(s.given_up_cents), (s.counts.given_up == null ? '' : rsPl(s.counts.given_up, 'item') + '. ') + (s.given_up_source === 'selfcheck' ? 'From the self-check.' : 'Worked out here.'), 'given_up')}
    </div>
    ${w.length ? `<div class="scroll"><table><thead><tr><th>Week of</th><th class="num">Recovered</th><th class="num">Lost</th><th class="num">At risk</th><th class="num">Given up</th></tr></thead><tbody>
      ${w.map(r => `<tr><td>${esc(r.week)}${r.frozen ? '' : ' <span class="small muted">still moving</span>'}</td><td class="num">${r.recovered_cents ? rsLink('returned:week:' + r.week, money(r.recovered_cents)) : money(0)}</td><td class="num">${r.lost_cents ? rsLink('lost:week:' + r.week, money(r.lost_cents)) : money(0)}</td><td class="num">${r.at_risk_cents == null ? '–' : money(r.at_risk_cents)}</td><td class="num">${money(r.given_up_cents)}</td></tr>`).join('')}
    </tbody></table></div>` : ''}
    <div class="note">Weeks freeze two weeks after they end, so a trend never changes after the fact; newer weeks say "still moving".</div></div>`;
}

function rsLiftHtml(d){
  const l = d.lift; if(!l) return '';
  const enough = l.enough;
  return `<div class="panel"><h2>Did the work make the difference?</h2>
    <div class="grid kpis">
      ${rsTile('Worked items paid', rsPct(l.worked.rate), `${rsNum(l.worked.paid)} of ${rsPl(l.worked.n, 'item')}`, 'lift:worked:paid')}
      ${rsTile('Similar unworked items paid', rsPct(l.unworked.rate), `${rsNum(l.unworked.paid)} of ${rsPl(l.unworked.n, 'item')}`, 'lift:unworked:paid')}
      ${rsTile('Money the work added', money(l.incremental_cents), `worked items brought ${money(l.worked.returned_cents)}; the unworked rate predicts ${money(l.predicted_cents)}`, 'lift:worked')}
    </div>
    <div class="note">${enough ? esc(l.basis) : 'Not enough cases yet: these rates need 20 worked and 20 unworked items. ' + esc(l.basis)}</div></div>`;
}

function rsRoiHtml(d){
  const r = d.roi; if(!r) return '';
  const head = r.costs_set ? `<div class="grid kpis">
      ${rsTile('Return on cost', r.roi == null ? '–' : r.roi + '×', `${money(r.returned_cents)} returned for ${money(r.cost_cents)} spent`, 'returned')}
      ${rsTile('Lift-adjusted return', r.roi_conservative == null ? '–' : r.roi_conservative + '×', `counting only what the work added, plus money found: ${money(r.conservative_cents)}`, 'returned:a')}
    </div>` : '<div class="note">Enter what RemitWatch costs each month (below, under Costs) to see the return on it.</div>';
  const rows = (r.months || []).slice(-12).reverse().map(x => `<tr><td>${esc(x.month)}</td><td class="num">${x.returned_cents ? rsLink('returned:month:' + x.month, money(x.returned_cents)) : money(0)}</td><td class="num">${money(x.cost_cents)}</td><td class="num">${x.roi == null ? '–' : x.roi + '×'}</td><td class="num">${x.roi_conservative == null ? '–' : x.roi_conservative + '×'}</td><td class="num small">${rsNum(x.fax_pages)} pages, ${rsNum(x.ai_calls)} AI calls</td></tr>`).join('');
  return `<div class="panel"><h2>Cost and return</h2>${head}
    ${rows ? `<div class="scroll"><table><thead><tr><th>Month</th><th class="num">Returned</th><th class="num">Cost</th><th class="num">Return</th><th class="num">Lift-adjusted</th><th class="num">Counted by the app</th></tr></thead><tbody>${rows}</tbody></table></div>` : ''}
    <div class="note">${esc(r.basis)}</div></div>`;
}

function rsWorkHtml(d){
  const w = d.work; if(!w) return '';
  const kinds = (w.kinds || []).map(k => `<tr><td>${esc(k.label)}</td><td class="num">${rsLink('work:' + k.key, rsNum(k.items))}</td><td class="num">${rsNum(k.n)}</td></tr>`).join('');
  const people = (w.people || []).slice(0, 12).map(p => `<tr><td>${esc(p.key)}</td><td class="num">${rsLink('work:person:' + p.key, rsNum(p.items))}</td><td class="num">${rsNum(p.n)}</td></tr>`).join('');
  const costs = (d.settings && d.settings.values && d.settings.values.costs) || {};
  const faxCost = Math.round((w.fax_pages || 0) * (costs.fax_page || 0) * 100);
  return `<div class="panel"><h2>Work done in RemitWatch</h2>
    <div class="grid two"><div><table><thead><tr><th>Kind of work</th><th class="num">Items</th><th class="num">Times</th></tr></thead><tbody>${kinds || '<tr><td colspan="3" class="muted">No work recorded yet.</td></tr>'}</tbody></table>
      <div class="small" style="margin-top:6px">Faxes sent: ${rsNum(w.faxes_sent)} (${rsNum(w.fax_pages)} pages${faxCost ? ', ' + money(faxCost) : ''}).
      Let go with a reason: ${rsLink('work:let_go', rsNum(w.let_go))}. Accepted by the owner's policy or a hide rule: ${rsLink('work:policy', rsNum(w.policy))}.</div></div>
    <div><table><thead><tr><th>Who</th><th class="num">Items</th><th class="num">Times</th></tr></thead><tbody>${people || '<tr><td colspan="3" class="muted">No one yet.</td></tr>'}</tbody></table></div></div></div>`;
}

function rsOutcomesHtml(d){
  const o = d.outcomes, live = d.live || {}; if(!o) return '';
  const st = live.states || {}, L = (d.labels && d.labels.states) || {};
  const states = Object.keys(L).map(k => `${rsTile(esc(L[k]), rsNum((st[k] || {}).n), money((st[k] || {}).cents), 'state:' + k)}`).join('');
  const closed = (o.closed_by || []).map(c => `<tr><td>${esc(c.label)}</td><td class="num">${rsLink('closed:' + c.key, rsNum(c.n))}</td><td class="num">${rsPct(c.rate)}</td></tr>`).join('');
  const cant = (o.cant_by || []).map(c => `<tr><td>${esc(c.key)}</td><td class="num">${rsLink('cant:' + c.key, rsNum(c.n))}</td></tr>`).join('');
  const wins = (o.win_by_kind || []).map(k => `<tr><td>${esc(k.label)}</td><td class="num">${rsNum(k.won)}</td><td class="num">${rsNum(k.lost)}</td><td class="num">${rsPct(k.rate)}</td></tr>`).join('');
  return `<div class="panel"><h2>The open book</h2>
    <div class="note">Every item that was ever in Work now or Waiting: ${rsLink('book', rsNum(o.book))}. Items by state right now:</div>
    <div class="grid kpis">${states}</div>
    <div class="grid two" style="margin-top:10px"><div>
      <div><b>${rsPct(o.closed_rate)}</b> closed (${rsLink('closed:any', rsNum(o.closed))}):</div>
      <table><tbody>${closed || '<tr><td class="muted">None yet.</td></tr>'}</tbody></table>
      <div style="margin-top:8px"><b>${rsPct(o.cant_rate)}</b> cannot be checked (${rsLink('cant:any', rsNum(o.cant_check))}), because:</div>
      <table><tbody>${cant || '<tr><td class="muted">None.</td></tr>'}</tbody></table></div>
    <div>
      <div>Worked and waiting with no money yet: ${rsLink('waiting_no_money', rsNum(o.waiting_no_money))} (${rsPct(o.waiting_no_money_rate)} of ${rsPl(o.worked, 'worked item')}${o.waiting_median_days != null ? `; ${Math.round(o.waiting_median_days)} days since the work, at the middle` : ''}).</div>
      <div style="margin-top:4px">Worked and denied again: ${rsLink('denied_again', rsNum(o.denied_again))} (${rsPct(o.denied_again_rate)}).</div>
      ${wins ? `<table style="margin-top:8px"><thead><tr><th>Kind of work</th><th class="num">Won</th><th class="num">Denied again</th><th class="num">Win rate</th></tr></thead><tbody>${wins}</tbody></table>` : ''}
    </div></div></div>`;
}

function rsHoursHtml(d){
  const h = d.hours; if(!h) return '';
  const can = d.owner_open;
  const rows = h.rows.map(r => `<tr><td>${esc(r.label)}</td><td class="num">${rsLink('hours:' + r.key, rsNum(r.n))}</td>
    <td class="num">${can ? `<input type="number" min="0" max="600" step="1" class="rs-min" data-k="${esc(r.key)}" value="${esc(r.minutes)}" style="width:5em" aria-label="Minutes">` : esc(r.minutes)}</td><td class="num">${rsNum(r.hours)}</td></tr>`).join('');
  return `<div class="panel"><h2>Hours saved <span class="small muted">(an estimate)</span></h2>
    <div class="scroll"><table><thead><tr><th>What RemitWatch did</th><th class="num">How many</th><th class="num">Minutes each</th><th class="num">Hours</th></tr></thead><tbody>${rows}
    <tr><td><b>Total</b></td><td></td><td></td><td class="num"><b>${rsNum(h.hours)}</b></td></tr></tbody></table></div>
    ${can ? '<div class="actions" style="margin-top:6px"><button class="primary" id="rsSaveMin">Save minutes</button><span class="small muted rs-msg" id="rsMinMsg"></span></div>'
      : '<div class="note">The owner sets the minutes (the owner\'s password, in the Providers tab).</div>'}</div>`;
}

function rsDenialsHtml(d){
  const x = d.denials; if(!x || !(x.months || []).length) return '';
  const months = x.months.slice(-12).reverse().map(m => `<tr><td>${esc(m.month)}</td><td class="num">${rsPct(m.line_rate)}</td><td class="num">${rsNum(m.denied)} of ${rsNum(m.answered)}</td><td class="num">${rsPct(m.dollar_rate)}</td></tr>`).join('');
  const groups = (x.by_group || []).slice(0, 10).map(g => {
    const last = (g.months || []).slice(-6).map(m => rsPct(m.line_rate)).join(' · ');
    return `<tr><td>${esc(g.key)}</td><td class="num">${rsPct(g.line_rate)}</td><td class="small">${esc(last)}</td></tr>`; }).join('');
  const reasons = r => (r || []).map(z => `<li>${esc(z.reason)} ${esc(z.meaning ? '(' + z.meaning.slice(0, 80) + ')' : '')}: ${rsNum(z.before)} to ${rsNum(z.recent)} lines</li>`).join('');
  const marks = (x.markers || []).map((mk, i) => `<li>${esc(mk.date)}: ${esc(mk.kind)} fix for ${esc(mk.payer_group || 'a payer')} (${rsLink('denial_fix:' + i, 'the item')}). Denied before: ${rsPct(mk.before.line_rate)}; after: ${rsPct(mk.after.line_rate)}.</li>`).join('');
  return `<div class="panel"><h2>Denials, month by month</h2><div class="note">${esc(x.definition || '')}</div>
    <div class="grid two"><div class="scroll"><table><thead><tr><th>Month</th><th class="num">Lines denied</th><th class="num">Lines</th><th class="num">Dollars denied</th></tr></thead><tbody>${months}</tbody></table></div>
    <div class="scroll"><table><thead><tr><th>Payer group</th><th class="num">Denied</th><th>Last six months</th></tr></thead><tbody>${groups}</tbody></table></div></div>
    <div class="grid two" style="margin-top:8px"><div><b>Rising reasons</b> (last 90 days against the 90 before)<ul class="small">${reasons(x.reasons && x.reasons.rising) || '<li class="muted">None.</li>'}</ul></div>
    <div><b>Falling reasons</b><ul class="small">${reasons(x.reasons && x.reasons.falling) || '<li class="muted">None.</li>'}</ul></div></div>
    ${marks ? `<div><b>Fixes recorded</b><ul class="small">${marks}</ul></div>` : '<div class="note">When someone records a credentialing, authorization or coding fix on an item, it shows here with the rate before and after.</div>'}
    <div class="small"><a href="#" id="rsClin">Denials by clinician</a> (the owner's password, when one is set)</div><div id="rsClinBox"></div></div>`;
}

function rsSpotHtml(d){
  const sp = d.spot || {}, s = sp.summary || {}, reasons = sp.reasons || {};
  const L = (d.labels && d.labels.signals) || {};
  const items = (sp.today || []).map(p => {
    const done = p.answer ? `<span class="chip">${p.answer === 'agree' ? 'Agreed' : 'Disagreed: ' + esc(reasons[p.reason] || p.reason || '')}</span> <span class="small muted">${esc(p.who || '')}</span>` : '';
    return `<div class="change rs-spot" data-id="${esc(p.finding_id)}" data-day="${esc(p.day || d.today)}"><div>
      <a href="#" class="rs-item" data-id="${esc(p.finding_id)}">${esc(L[p.signal] || p.signal || 'Item')}</a>
      <div class="small muted">${esc((sp.strata || {})[p.stratum] || p.stratum)} · ${esc(p.payer_group || '')} · ${money(p.cents)}</div>
      ${p.basis ? `<div class="small">${esc(p.basis)}</div>` : ''} ${done}
      <div class="rs-dis" hidden><select class="rs-reason" aria-label="What is wrong">${Object.entries(reasons).map(([k, v]) => `<option value="${esc(k)}">${esc(v)}</option>`).join('')}</select>
        <input class="rs-note" maxlength="500" placeholder="What did you find? (optional)" style="width:min(100%,22em)"> <button class="rs-send">Save</button></div>
      <div class="small sev-high rs-err" role="alert"></div></div>
      <div class="actions"><button class="rs-agree">Agree</button><button class="rs-disagree">Disagree</button></div></div>`;
  }).join('');
  const strata = (s.by_stratum || []).map(r => `<tr><td>${esc(r.label)}</td><td class="num">${rsPct(r.rate)}</td><td class="num">${rsNum(r.agree)} of ${rsNum(r.n)}</td></tr>`).join('');
  const weeks = (s.by_week || []).slice(-8).reverse().map(r => `<tr><td>${esc(r.key)}</td><td class="num">${rsPct(r.rate)}</td><td class="num">${rsNum(r.agree)} of ${rsNum(r.n)}</td></tr>`).join('');
  const dis = (s.disagreements || []).slice(-10).reverse().map(x => `<li>${esc(x.day)}: <a href="#" class="rs-item" data-id="${esc(x.finding_id)}">${esc(L[x.signal] || x.signal || 'item')}</a>, ${esc(x.reason_label || x.reason || '')}${x.note ? ': ' + esc(x.note) : ''}</li>`).join('');
  return `<div class="panel"><div class="panel-head"><h2>Spot checks</h2><span class="small muted">check each against EMA and the payer, then say if RemitWatch is right</span></div>
    ${sp.error ? `<div class="warnbox">${esc(sp.error)}</div>` : ''}
    <div class="small" style="margin-bottom:6px"><label>Your name <input id="rsWho" maxlength="80" value="${esc(rsName())}" placeholder="optional" style="width:12em"></label></div>
    ${items || '<div class="muted">No item to check today.</div>'}
    <div class="grid two" style="margin-top:10px"><div><b>${rsPct(s.rate)}</b> agreement (${rsNum(s.agree)} of ${rsNum(s.answered)} answered)
      <table><thead><tr><th>Picked from</th><th class="num">Agreed</th><th class="num">Checks</th></tr></thead><tbody>${strata || '<tr><td colspan="3" class="muted">No answers yet.</td></tr>'}</tbody></table></div>
    <div><table><thead><tr><th>Week of</th><th class="num">Agreed</th><th class="num">Checks</th></tr></thead><tbody>${weeks || '<tr><td colspan="3" class="muted">No answers yet.</td></tr>'}</tbody></table></div></div>
    ${dis ? `<div style="margin-top:8px"><b>Recent disagreements</b> (each goes to the people who fix the rules)<ul class="small">${dis}</ul></div>` : ''}</div>`;
}

function rsCostsHtml(d){
  const s = d.settings || {}, v = (s.values || {}).costs || {}, labels = d.cost_labels || {};
  const can = d.owner_open;
  const rows = Object.keys(labels).map(k => `<tr><td>${esc(labels[k])}</td><td class="num">${can ? `$<input type="number" min="0" step="0.01" class="rs-cost" data-k="${esc(k)}" value="${esc(v[k])}" style="width:7em" aria-label="${esc(labels[k])}">` : '$' + esc(v[k])}</td></tr>`).join('');
  const hist = (s.history || []).slice(-8).reverse().map(h => `<li>${esc(String(h.at || '').slice(0, 16).replace('T', ' '))} by ${esc(h.by || 'someone')}</li>`).join('');
  return `<div class="panel"><h2>Costs</h2>
    <table><tbody>${rows}</tbody></table>
    ${can ? '<div class="actions" style="margin-top:6px"><button class="primary" id="rsSaveCost">Save costs</button><span class="small muted" id="rsCostMsg"></span></div>'
      : '<div class="note">The owner sets the costs (the owner\'s password, in the Providers tab).</div>'}
    ${hist ? `<details class="small"><summary>Earlier versions</summary><ul>${hist}</ul></details>` : ''}</div>`;
}

// ---------------------------------------------------------------- the page
function rsStyle(){
  if(document.getElementById('rsStyle')) return;
  const el = document.createElement('style'); el.id = 'rsStyle';
  el.textContent = '.rs-top{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap;margin:4px 0 2px}.rs-big{font-size:34px;font-weight:750;line-height:1.1}'
    + '.rs-top a{text-decoration:none;color:var(--ink)}.rs-tile:focus-visible{outline:2px solid var(--accent)}.rs-spot .actions{flex:0 0 auto}'
    + '@media(max-width:640px){.rs-big{font-size:28px}.rs-spot{flex-wrap:wrap}}';
  document.head.appendChild(el);
}

async function resultsTab(v){
  rsStyle();
  const d = await api('/api/results');
  let score = null; try { score = await api('/api/results/scoreboard'); } catch(e){}
  state.rsData = d;
  const asOf = d.as_of ? `Figures as of ${String(d.as_of).slice(0, 16).replace('T', ' ')} UTC` : 'Not worked out yet';
  v.innerHTML = `<div class="page-head"><h1 class="page-title">Results</h1><span class="small muted">${esc(asOf)}. Work recorded since then shows after the next analysis.</span></div>
    ${(d.errors || []).length ? `<div class="warnbox">Part of these figures could not be worked out: ${esc(d.errors[0])}</div>` : ''}
    ${rsMoneyHtml(d)}${rsRoiHtml(d)}${rsScoreHtml(score)}${rsLiftHtml(d)}${rsWorkHtml(d)}${rsOutcomesHtml(d)}${rsHoursHtml(d)}${rsDenialsHtml(d)}${rsSpotHtml(d)}${rsCostsHtml(d)}`;
  rsBind(v, d);
  if(state.resultsOpen){ const k = RS_KEY_OF[state.resultsOpen] || state.resultsOpen; state.resultsOpen = null; rsOpenList(k, rsTitle(k)); }
}

function rsTitle(key){
  const t = {returned: 'Money returned on claims RemitWatch flagged or worked', 'returned:a': 'After RemitWatch work', 'returned:b': 'Found by RemitWatch',
    'returned:c': 'Flagged, no recorded work', taken_back: 'Taken back after the flag', lost: 'Lost to deadlines', at_risk: 'At risk in the next 14 days',
    given_up: 'Given up without a decision', book: 'The open book', 'closed:any': 'Closed', 'cant:any': 'Cannot be checked',
    waiting_no_money: 'Worked, waiting, no money yet', denied_again: 'Worked and denied again'};
  return t[key] || key.replace(/^state:/, 'Items in ').replace(/^returned:(week|month):/, 'Money returned in ').replace(/^work:/, 'Work: ').replace(/:/g, ' ');
}

function rsBind(v, d){
  v.querySelectorAll('.rs-open').forEach(a => a.onclick = e => { e.preventDefault(); rsOpenList(a.dataset.key, rsTitle(a.dataset.key)); });
  v.querySelectorAll('.rs-tile').forEach(t => { if(!t.dataset.key) return;
    const go = () => rsOpenList(t.dataset.key, rsTitle(t.dataset.key));
    t.onclick = go; t.onkeydown = e => { if(e.key === 'Enter' || e.key === ' '){ e.preventDefault(); go(); } }; });
  v.querySelectorAll('.rs-item').forEach(a => a.onclick = e => { e.preventDefault(); openDetail(a.dataset.id); });
  const who = v.querySelector('#rsWho'); if(who) who.onchange = () => rsSetName(who.value.trim());
  v.querySelectorAll('.rs-spot').forEach(row => {
    const send = async (answer, reason, note) => {
      const err = row.querySelector('.rs-err'); err.textContent = '';
      try { await rsSend('/api/results/spot', 'POST', {day: row.dataset.day, finding_id: row.dataset.id, answer, reason, note, name: (who && who.value.trim()) || ''}); render(); }
      catch(e){ err.textContent = e.message; }
    };
    row.querySelector('.rs-agree').onclick = () => send('agree');
    row.querySelector('.rs-disagree').onclick = () => { row.querySelector('.rs-dis').hidden = false; row.querySelector('.rs-reason').focus(); };
    row.querySelector('.rs-send').onclick = () => send('disagree', row.querySelector('.rs-reason').value, row.querySelector('.rs-note').value.trim());
  });
  const saveVals = async (group, sel, msgId) => {
    const vals = {}; v.querySelectorAll(sel).forEach(i => { vals[i.dataset.k] = Number(i.value); });
    const msg = v.querySelector('#' + msgId);
    try { await rsSend('/api/results/settings', 'PUT', {values: {[group]: vals}, base_version: (d.settings || {}).version || '', name: rsName()}); render(); }
    catch(e){ if(msg) msg.textContent = e.message; }
  };
  const sm = v.querySelector('#rsSaveMin'); if(sm) sm.onclick = () => saveVals('minutes', '.rs-min', 'rsMinMsg');
  const sc = v.querySelector('#rsSaveCost'); if(sc) sc.onclick = () => saveVals('costs', '.rs-cost', 'rsCostMsg');
  const cl = v.querySelector('#rsClin'); if(cl) cl.onclick = async e => { e.preventDefault();
    const box = v.querySelector('#rsClinBox');
    try {
      const h = {}; if(state.ownerKey) h['X-Owner-Password'] = state.ownerKey;
      const r = await fetch('/api/results/denials/clinicians', {headers: h}); const j = await r.json();
      if(!r.ok) throw new Error(j.detail || 'Refused.');
      box.innerHTML = `<div class="scroll"><table><thead><tr><th>Clinician</th><th class="num">Lines</th><th class="num">Denied</th><th class="num">Dollars denied</th></tr></thead><tbody>${(j.clinicians || []).map(c => `<tr><td>${esc(c.name || c.npi || 'Unknown')}</td><td class="num">${rsNum(c.answered)}</td><td class="num">${rsPct(c.line_rate)}</td><td class="num">${rsPct(c.dollar_rate)}</td></tr>`).join('')}</tbody></table></div>`;
    } catch(err){ box.innerHTML = `<div class="note">${esc(err.message)}</div>`; }
  };
}
