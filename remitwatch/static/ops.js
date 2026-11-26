// RemitWatch Data tab: the three panels of the parts that run by themselves (remitwatch.selfcheck, runlog, repair, diagnose,
// archive), drawn into the empty <section id="ops-panels"> data.js leaves for them, after (on the Vercel site only) the
// panel "Analysis on this site" with its "Run the analysis now" button (the owner's decision 1: that site analyses only on
// a press; the server analyses on its own schedule and shows no such panel):
//   "Running on its own"       the checks every run makes and their history, safe mode, the weekly self-audit and its lists,
//                              money given up without a decision, what the app cannot see, the self-regulation
//   "Logs and diagnostics"     the last runs in words, problems the self-repair could not fix, the diagnostic bundle
//   "Store size and memory"    the store's size and memory against their limits, the archive, the horizon
// Every figure that stands for a list opens that list. Loaded after data.js; uses index.html's helpers ($, api, esc, money,
// openDetail, notice, state) and today.js's pl. Names here start with op (one global scope with the other scripts).

const OP_STATUS = {ok: ['Passed', 'ok'], fail: ['Failing', 'sev-high'], error: ['Could not run', 'sev-high'], skip: ['Not checked', 'muted'],
  warn: ['Warning', 'sev-medium']};
const OP_OUTCOME = {ok: 'finished', partial: 'finished with errors it contained', failed: 'failed', killed: 'was stopped before it finished',
  skipped: 'was skipped', safe_mode: 'finished in safe mode', running: 'is running'};

function opPl(n, word){ return typeof pl === 'function' ? pl(n, word) : `${n} ${word}${n === 1 ? '' : 's'}`; }
function opWhen(iso){ return iso ? esc(String(iso).slice(0, 16).replace('T', ' ')) : ''; }
function opNarrow(){ return typeof window !== 'undefined' && window.innerWidth > 0 && window.innerWidth <= 640; }

async function opPanels(el){
  el.innerHTML = '<div class="panel"><h2>Running on its own</h2><div class="muted small">loading…</div></div>';
  let p;
  try { p = await api('/api/ops/panels'); }
  catch(e){ el.innerHTML = `<div class="panel"><h2>Running on its own</h2><div class="note">These panels did not come back (${esc(e.message || 'no answer')}). Reload in a minute.</div></div>`; return; }
  el.innerHTML = opAnalysisHtml(p.analysis || {}) + opSelfcheckHtml(p.selfcheck || {}) + opLogsHtml(p.logs || {}) + opStoreHtml(p.archive || {});
  opBind(el, p);
  opBindRun(el);
}

// "Analysis on this site" (Vercel only): when the site last analysed, that it analyses only on a press, a run going now,
// what the last run could not check (open items and dollars), and the button that starts the run chain.
function opAnalysisHtml(a){
  if(!a.press_only) return '';
  const an = a.analysed || {}, run = a.running, un = a.unchecked;
  const last = an.at ? `This site last analysed ${opWhen(an.at)} UTC${an.findings != null ? `, ${esc(opPl(Number(an.findings), 'item'))} on its lists` : ''}.`
    : 'This site has not analysed yet.';
  return `<div class="panel" id="opAnalysis"><div class="panel-head"><h2>Analysis on this site</h2>
      <span class="small muted">${esc(a.text || '')}</span></div>
    <div class="small"><b>${last}</b></div>
    ${run ? `<div class="note small">A run is going (asked for or started ${opWhen(run.since)} UTC). It finishes by itself, and the lists change when it has.</div>` : ''}
    ${a.failed && a.failed.text ? `<div class="warnbox small"><b class="sev-high">The last run stopped before it finished.</b> ${esc(a.failed.text)}</div>` : ''}
    ${un && un.text ? `<div class="warnbox small"><b>What the last run could not check.</b> ${esc(un.text)}</div>` : ''}
    <div style="margin-top:8px"><button class="primary" data-op-press="1" ${run ? 'disabled' : ''}>Run the analysis now</button>
      <span class="small muted" data-op-press-said></span></div>
  </div>`;
}

// (its own attribute: the owner's "Accept what run ... found" carries data-op-run, the id of the run it accepts)
function opBindRun(el){
  const b = el.querySelector('#opAnalysis [data-op-press]');
  if(!b) return;
  b.addEventListener('click', async () => {
    const said = el.querySelector('#opAnalysis [data-op-press-said]');
    b.disabled = true; if(said) said.textContent = 'Asking…';
    try { const r = await api('/api/ops/run', {method: 'POST'}); if(said) said.textContent = r.text || (r.started ? 'Started.' : '');
      if(r.started && typeof refreshHeader === 'function') setTimeout(refreshHeader, 3000); }
    catch(e){ if(said) said.textContent = 'Not started: ' + (e.message || 'no answer'); b.disabled = false; }
  });
}

function opSelfcheckHtml(s){
  const checks = s.checks || [], safe = s.safe_mode || {}, au = (s.audit || {}).latest || {}, last = s.last_check || {};
  const rows = checks.map(c => {
    const st = OP_STATUS[c.status] || [c.status || '', ''];
    const many = (c.status === 'fail' && c.n) ? `<button class="linkish" data-op-check="${esc(c.id)}">${esc(opPl(c.n, 'item'))}${c.cents ? ', ' + money(c.cents) : ''}</button>` : '';
    const hist = (c.history || []).map(h => h === 'ok' ? '·' : h === 'fail' ? '✗' : h === 'error' ? '!' : '-').join('');
    return `<div class="check"><div><b>${esc(c.title || c.id)}</b> <span class="${st[1]}">${esc(st[0])}</span> ${many}
      <div class="small muted">${esc(c.status === 'ok' ? (c.sentence || '') : (c.what_to_check || c.sentence || ''))}</div>
      ${hist ? `<div class="small muted" title="The last runs, oldest first: · passed, ✗ failed, ! could not run">Last runs: ${esc(hist)}</div>` : ''}
      <div class="op-list" data-op-list="${esc(c.id)}"></div></div></div>`;
  }).join('');
  const failing = checks.filter(c => c.status === 'fail').map(c => c.id);
  const accept = failing.length && last.run_id ? `<div class="small" style="margin-top:6px">If these changes are what you intended (a new rule, a payer's new policy), the owner can accept exactly what this run found:
      <button class="btn" data-op-accept="${esc(failing.join(','))}" data-op-run="${esc(last.run_id)}">Accept what run ${esc(last.run_id)} found</button></div>` : '';
  const audit = au.today ? `<div class="check"><div><b>The weekly self-audit</b> <span class="small muted">of ${esc(au.today)}</span>
      <div class="small">${au.gaps_n ? `<button class="linkish" data-op-audit="gap">${esc(opPl(au.gaps_n, 'line'))} with money to recover and no item (${money(au.gaps_cents)})</button>` : 'Every line with money to recover has an item.'}
      ${au.wasted_n ? ` · <button class="linkish" data-op-audit="wasted">${esc(opPl(au.wasted_n, 'item'))} on Work now that look settled</button>` : ''}</div>
      <div class="op-list" data-op-list="audit"></div></div></div>` : '<div class="small muted">The weekly self-audit has not run yet: it runs in the first run of the week that does not analyse.</div>';
  return `<div class="panel" id="opRunning"><div class="panel-head"><h2>Running on its own</h2>
      <span class="small muted">After every run RemitWatch checks its own work: nothing closed without proof, nothing dropped without a reason, every dollar accounted for.</span></div>
    ${safe.on ? `<div class="warnbox"><b>Safe mode.</b> ${esc(safe.text || '')}</div>` : ''}
    ${(s.lines || []).map(t => `<div class="small">${esc(t)}</div>`).join('')}
    <details class="dfold" ${failing.length ? 'open' : ''}><summary>${esc(opPl(checks.length, 'check'))} after the last run</summary>${rows}${accept}</details>
    ${audit}
    <div class="small"><button class="btn" data-op-audit-now="1">Run the self-audit now</button> <span class="small muted" data-op-audit-said></span></div>
  </div>`;
}

function opLogsHtml(l){
  const runs = (l.runs || []).slice(0, 10), probs = l.open_problems || [];
  const runRow = r => {
    const words = OP_OUTCOME[r.outcome] || r.outcome || '';
    const errs = Number(r.errors || 0) + Number(r.logged_errors || 0);
    const bits = [r.seconds != null ? `${esc(r.seconds)} s` : '', r.peak_mb ? `peak ${Number(r.peak_mb).toLocaleString()} MB` : '',
      errs ? `${esc(opPl(errs, 'error'))} contained` : '', r.repairs ? `${esc(opPl(r.repairs, 'repair'))}` : '',
      r.outcome === 'killed' && r.last_step ? `stopped during ${esc(r.last_step)}` : ''].filter(Boolean).join(' · ');
    return opNarrow() ? `<div class="check"><div><b>${opWhen(r.started_at)}</b> ${esc(words)}<div class="small muted">${bits}</div></div></div>`
      : `<tr><td class="small">${opWhen(r.started_at)}</td><td>${esc(words)}</td><td class="small">${bits}</td></tr>`;
  };
  const table = runs.length ? (opNarrow() ? runs.map(runRow).join('')
    : `<table><thead><tr><th>Started</th><th>Outcome</th><th>Time, memory, errors</th></tr></thead><tbody>${runs.map(runRow).join('')}</tbody></table>`)
    : '<div class="small muted">No run has been logged yet.</div>';
  const problems = probs.map(x => `<div class="warnbox small"><b>${esc(x.what || x.target || 'A problem')}</b>${x.since ? ` since ${opWhen(x.since)}` : ''}. ${esc(x.next_step || '')}</div>`).join('');
  const bundle = (l.bundle || {});
  return `<div class="panel" id="opLogs"><div class="panel-head"><h2>Logs and diagnostics</h2>
      <span class="small muted">${esc(l.sentence || '')}</span></div>
    ${l.error ? `<div class="note">${esc(l.error)}</div>` : ''}
    ${problems}
    ${table}
    <div style="margin-top:8px"><a class="btn" href="/api/ops/diagnostic-bundle?days=7" download>Download the diagnostic bundle</a>
      <div class="small muted">${esc(bundle.what || 'Give this file and the repository to Claude to find the cause of a problem. It holds no patient data.')}</div></div>
  </div>`;
}

function opStoreHtml(a){
  const health = (a.health || []).filter(h => h && h.title);
  const hist = (a.history || []).slice(-7).reverse();
  const histRows = hist.map(h => `<div class="small">${esc(h.day || String(h.at || '').slice(0, 10))}: the store ${h.hot_mb != null ? Number(h.hot_mb).toLocaleString() + ' MB' : '?'}${h.peak_mb ? `, peak memory ${Number(h.peak_mb).toLocaleString()} MB` : ''}${h.share != null ? ` (${Math.round(h.share * 100)}% of its limit)` : ''}${h.retired && h.retired.bills ? `; ${Number(h.retired.bills).toLocaleString()} older claims moved to the archive` : ''}</div>`).join('');
  return `<div class="panel" id="opStore"><div class="panel-head"><h2>Store size and memory</h2>
      <span class="small muted">Older settled claims move to an archive the daily run never loads; Find a claim still opens them.</span></div>
    ${(a.lines || []).map(t => `<div class="small">${esc(t)}</div>`).join('') || '<div class="small muted">No run has measured the store yet.</div>'}
    ${health.map(h => `<div class="check"><div><span class="${h.ok ? 'ok' : 'sev-medium'}">${esc(h.title)}</span>${h.detail ? `<div class="small muted">${esc(h.detail)}</div>` : ''}${h.who ? `<div class="small muted">${esc(h.who)}</div>` : ''}</div></div>`).join('')}
    ${histRows ? `<details class="dfold"><summary>The last runs</summary>${histRows}</details>` : ''}
  </div>`;
}

function opItemsHtml(items, label){
  if(!items || !items.length) return '<div class="small muted">Nothing to list.</div>';
  return `<div class="small muted">${esc(label)}</div>` + items.slice(0, 200).map(x => {
    const fid = x.finding_id || (/^[0-9a-f]{24}$/.test(String(x.key || '')) ? x.key : null);
    const cents = x.cents != null ? money(x.cents) : (x.shortfall_cents != null ? money(x.shortfall_cents) : '');
    const what = x.category ? esc(String(x.category).replace(/_/g, ' ')) : (x.accepted ? 'accepted' : '');
    return `<div class="small">${fid ? `<button class="linkish" data-op-open="${esc(fid)}">Open the item</button>` : esc(x.reason || 'a line with no item')} ${cents} ${what}</div>`;
  }).join('') + (items.length > 200 ? `<div class="small muted">and ${esc(opPl(items.length - 200, 'more'))}</div>` : '');
}

function opBind(el, p){
  el.querySelectorAll('[data-op-check]').forEach(b => b.addEventListener('click', async () => {
    const id = b.getAttribute('data-op-check'), box = el.querySelector(`[data-op-list="${id}"]`);
    if(!box) return;
    if(box.innerHTML){ box.innerHTML = ''; return; }
    box.innerHTML = '<div class="small muted">loading…</div>';
    try { const r = await api('/api/ops/selfcheck/offenders/' + encodeURIComponent(id)); box.innerHTML = opItemsHtml(r.items, `What run ${r.run_id || ''} found, largest first:`); opBindOpen(box); }
    catch(e){ box.innerHTML = `<div class="small sev-high">The list did not come back (${esc(e.message || 'no answer')}).</div>`; }
  }));
  el.querySelectorAll('[data-op-audit]').forEach(b => b.addEventListener('click', async () => {
    const which = b.getAttribute('data-op-audit'), box = el.querySelector('[data-op-list="audit"]');
    if(!box) return;
    box.innerHTML = '<div class="small muted">loading…</div>';
    try { const r = await api('/api/ops/selfcheck/audit/' + which); box.innerHTML = opItemsHtml(r.items, which === 'gap' ? 'Lines with money to recover and no item working them:' : 'Items on Work now that look settled already: check them before working them.'); opBindOpen(box); }
    catch(e){ box.innerHTML = `<div class="small sev-high">The list did not come back (${esc(e.message || 'no answer')}).</div>`; }
  }));
  const now = el.querySelector('[data-op-audit-now]');
  if(now) now.addEventListener('click', async () => {
    const said = el.querySelector('[data-op-audit-said]'); now.disabled = true; if(said) said.textContent = 'Checking…';
    try { const r = await api('/api/ops/selfcheck/audit', {method: 'POST'}); if(said) said.textContent = r.asked ? r.text : 'Done: the panel shows the new figures.'; if(r.ran) opPanels(el); }
    catch(e){ if(said) said.textContent = 'It did not run: ' + (e.message || 'no answer'); now.disabled = false; }
  });
  el.querySelectorAll('[data-op-accept]').forEach(b => b.addEventListener('click', async () => {
    const checks = b.getAttribute('data-op-accept').split(',').filter(Boolean), run = b.getAttribute('data-op-run');
    if(!confirm('Accept exactly what this run found? The items it lists leave the worklist as the newest analysis says.')) return;
    b.disabled = true;
    try { const r = await api('/api/ops/selfcheck/accept', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({checks, run_id: run})});
      notice(r.stale ? (r.text || 'The checks ran again after this page was opened: look at the new list first.') : (r.text || 'Accepted.')); opPanels(el); }
    catch(e){ notice('Not accepted: ' + (e.message || 'no answer')); b.disabled = false; }
  }));
}

function opBindOpen(box){
  box.querySelectorAll('[data-op-open]').forEach(b => b.addEventListener('click', () => { if(typeof openDetail === 'function') openDetail(b.getAttribute('data-op-open')); }));
}

// ---------------------------------------------------------------- what the archive took (remitwatch.archive)
// An older, settled claim or item moved to the archive opens from it (Find a claim, an item's link): it says so, since
// when, and offers to bring it back to the lists with the next analysis (POST /api/archive/reopen).
function opArchivedHtml(text, at){
  return `<div class="note" data-op-archived="1"><b>In the archive${at ? ' since ' + esc(String(at).slice(0, 10)) : ''}.</b> ${esc(text || '')}
    <div style="margin-top:6px"><button class="btn" data-op-reopen="1">Bring it back to the lists</button> <span class="small muted" data-op-reopen-said></span></div></div>`;
}

function opBindReopen(root, body){
  const b = root.querySelector('[data-op-reopen]');
  if(!b) return;
  b.addEventListener('click', async () => {
    const said = root.querySelector('[data-op-reopen-said]');
    b.disabled = true;
    try { const r = await api('/api/archive/reopen', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(Object.assign({reason: 'brought back from the page'}, body))});
      if(said) said.textContent = r.text || 'Asked: it comes back with the next analysis.'; }
    catch(e){ if(said) said.textContent = 'Not asked: ' + (e.message || 'no answer'); b.disabled = false; }
  });
}

// An item the archive took, opened by its id (the item's panel): its own facts, its history ending in why it left, the
// whole claim, and the button that brings it back. Its status cannot be changed while it is in the archive.
function opArchivedItem(d, r){
  const f = r.finding || {}, events = r.events || [], last = events[events.length - 1] || {};
  const plain = (typeof labelOf === 'function' && state.brief && state.brief.labels) ? (labelOf(state.brief, f.signal).label || f.signal) : (f.title || f.signal || 'An item');
  d.innerHTML = `<div class="dr-head"><div><h2>${esc(plain)}</h2><div class="small muted">${esc([f.payer_group, f.procedure_code, f.service_date].filter(Boolean).join(' · '))}${f.shortfall_cents ? ' · ' + money(f.shortfall_cents) : ''}</div></div><button id="closeD" aria-label="Close">✕</button></div>
    ${opArchivedHtml(last.kind === 'archived' ? last.note : '', r.archived_at)}
    ${r.bill ? '<div class="small" style="margin:6px 0"><a href="#" id="opArchivedClaim">See the whole claim: every remittance, line and adjustment</a></div>' : ''}
    <details class="dfold"><summary>Its history</summary>${events.map(e => `<div class="small">${opWhen(e.at)} ${esc(String(e.kind || '').replace(/_/g, ' '))}${e.to_status ? ' · ' + esc(e.to_status) : ''}</div>`).join('') || '<div class="small muted">Nothing recorded.</div>'}</details>`;
  const c = d.querySelector('#closeD'); if(c && typeof closeDetail === 'function') c.onclick = closeDetail;
  const go = d.querySelector('#opArchivedClaim'); if(go) go.onclick = e => { e.preventDefault(); if(typeof openClaim === 'function') openClaim(r.bill, f.finding_id); };
  opBindReopen(d, {finding_id: f.finding_id});
}

// data.js draws the Data tab and leaves <section id="ops-panels"> empty: fill it each time it appears
(function opWatch(){
  if(typeof document === 'undefined' || typeof MutationObserver === 'undefined') return;
  const fill = () => { const el = document.getElementById('ops-panels'); if(el && !el.dataset.opFilled){ el.dataset.opFilled = '1'; opPanels(el); } };
  new MutationObserver(fill).observe(document.documentElement, {childList: true, subtree: true});
  fill();
})();
