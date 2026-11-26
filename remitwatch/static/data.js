// RemitWatch Data tab: whether the numbers can be trusted, the feeds, and EMA's reports: what each report was read as,
// the payments EMA posted that no remittance carries (with the request to send them again), payments the payer sent that
// EMA never posted (with the list of them to post), credits (with the list of them to refund), the A/R accounted for,
// what the last run noted, and the write-off patterns waiting for the owner. The checklist, the feeds and the fixed
// panels draw at once; EMA's panels fill their place when their answer comes. A link from another page lands on what it
// names (state.dataAnchor). Loaded after today.js; uses index.html's helpers ($, api, esc, money, money2, qs, state,
// render, openDetail) and today.js's (longDate, pl, dataChecks, tdBlindBanner, tdBindLinks) and features.js's feedPanel.
// Every number that opens a list opens one whose count line shows that same number. Names here start with dt (one global
// scope with the other scripts).

const DT_REPORT = {ar_report: 'A/R report', production: 'Production report'};
const DT_STATUS = {current: ['Current', 'ok'], partly_replaced: ['Current, partly replaced', 'ok'], replaced: ['Replaced by a newer file', 'muted'],
  superseded: ['Replaced by a newer file', 'muted'], held: ['Held: needs a decision', 'sev-high'], waiting: ['Waiting to be read', 'sev-medium'],
  reread: ['Waiting to be read again', 'sev-medium'], refused: ['Refused', 'sev-high'], discarded: ['Discarded', 'muted']};
const DT_SHOW = 8;          // catalog rows shown before "Show all"
const DT_ASK_SHOW = 12;     // re-send requests shown before "Show all" (newest month first, most dollars first)

// a day as written, or a stored moment (a decision, a check, a file read, a run) on the practice's day (today.js practiceDate)
function dtDay(d){ return d ? longDate(typeof practiceDate === 'function' ? practiceDate(d) : String(d).slice(0, 10)) : ''; }
// a phone (390 px): lists instead of wide tables, so each row's button stays on screen
function dtNarrow(){ return typeof window !== 'undefined' && window.innerWidth > 0 && window.innerWidth <= 640; }
function dtAge(days, limit){ if(days == null) return ''; const cls = days > limit ? 'sev-high' : 'ok'; return `<span class="${cls}">${esc(pl(days, 'day'))} old</span>`; }
function dtScope(f){
  if(f.scope === 'posted' && (f.posted_lo || f.posted_hi)) return `posted ${dtDay(f.posted_lo)} to ${dtDay(f.posted_hi)}`;
  if(f.svc_lo || f.svc_hi){ const a = String(f.svc_lo||'').slice(0,4), b = String(f.svc_hi||'').slice(0,4);
    return a && a === b ? `service year ${esc(a)}` : `service ${dtDay(f.svc_lo)} to ${dtDay(f.svc_hi)}`; }
  return '';
}
function dtCopy(text, btn){
  const done = ok => { if(btn){ const was = btn.textContent; btn.textContent = ok ? 'Copied' : 'Select and copy it below'; setTimeout(()=>{ btn.textContent = was; }, 2500); } };
  try { if(navigator.clipboard && navigator.clipboard.writeText){ navigator.clipboard.writeText(text).then(()=>done(true), ()=>done(false)); return; } } catch(e){}
  done(false);
}

async function dataTab(v){
  const m = state.meta;
  if(!m){ v.innerHTML = '<div class="panel"><h2>Data</h2><div class="note">The site did not answer with its settings. Reload the page in a minute.</div></div>'; return; }
  const covP = api('/api/coverage').catch(()=>null), emaP = api('/api/ema/data').catch(e=>({ready:false, error: e.message || 'no answer from the site'}));
  const anchor = state.dataAnchor; state.dataAnchor = null;
  v.innerHTML = `<div id="dtBlind"></div><div id="checks"></div><div id="feedPanel"></div>
    <div id="dtEma"><div class="panel"><h2>EMA's reports</h2><div class="muted small">loading…</div></div></div>
    <section id="ops-panels"></section>
    <div id="dtGaps"></div>
    <div class="grid two">
      <div class="panel"><h2>What is loaded</h2><table>
        <tr><td>Files ingested</td><td class="num">${((m.files||{}).n||0).toLocaleString()} (${(m.files||{}).n835||0} remits, ${(m.files||{}).nema||0} EMA exports)</td></tr>
        <tr><td>Last ingest</td><td class="num">${esc((m.files||{}).last_ingest||'–')}</td></tr>
        <tr><td>Remittances</td><td class="num">${((m.remits||{}).n||0).toLocaleString()} · ${money((m.remits||{}).total_cents)}</td></tr>
        <tr><td>Payment dates</td><td class="num">${esc((m.remits||{}).first_payment||'–')} to ${esc((m.remits||{}).last_payment||'–')}</td></tr>
        <tr><td>Service lines</td><td class="num">${((m.lines||{}).n||0).toLocaleString()} · service dates ${esc((m.lines||{}).first_dos||'–')} to ${esc((m.lines||{}).last_dos||'–')}</td></tr>
        ${(m.ema||[]).map(e=>`<tr><td>EMA ${esc(e.family)}</td><td class="num">${Number(e.n||0).toLocaleString()} rows · service dates ${esc(e.first_dos)} to ${esc(e.last_dos)}</td></tr>`).join('')}
      </table></div>
      <div class="panel"><h2>Analysis runs</h2><table><thead><tr><th>Run</th><th>Finished</th><th class="num">Lines</th><th class="num">Findings</th></tr></thead><tbody>${(m.runs||[]).map(r=>`<tr><td class="small">${esc(r.run_id)}</td><td class="small">${esc(r.finished_at)}</td><td class="num">${Number(r.lines||0).toLocaleString()}</td><td class="num">${Number(r.findings||0).toLocaleString()}</td></tr>`).join('')}</tbody></table></div>
    </div>
    <details class="panel fold"><summary><b>Data the practice still needs to supply</b></summary>
      <ol class="small">
        <li><b>The TriZetto remittance files every week.</b> Everything on this site is only as current as the newest file in the folder. TriZetto can also deliver them to an SFTP mailbox; ask first whether that would take them away from ModMed.</li>
        <li><b>EMA's A/R and Production reports every Monday</b>, and the three weekly exports: the Upload page lists each one, with its filters and columns.</li>
        <li><b>Executed payer contracts and fee schedules</b> with effective dates, Anthem commercial and Humana Medicare Advantage first, then UnitedHealthcare, Aetna, Cigna and UMWA, so "below contract" can replace "below the payer's usual rate".</li>
        <li><b>ModMed's all-patient EHI export, once</b> (free; request it through ModMed support). Nothing on this site reads it yet; it is the next reader to build once a copy is in hand.</li>
        <li><b>The remittance files for any weeks listed as missing</b>, from TriZetto.</li>
        <li><b>Zelis / Optum Pay / Payspan enrollment status</b> for each payer, to stop fee-bearing payments.</li>
        <li><b>Provider roster</b> (NPI, credential, start date) to replace the inferred physician / non-physician tiers.</li>
        <li><b>The EMA appointment export</b> (scheduled, arrived, no-show, cancelled, with clinician and office). Nothing on this site reads it yet.</li>
        <li><b>Remittances for VA community care</b>, if TriZetto can supply them. None has ever arrived, so those visits are in no count here.</li>
        <li><b>The 2026 MIPS payment adjustment file</b> (qpp.cms.gov, Performance Feedback).</li>
      </ol>
      <div class="note">Not needed: 837 claim files (the remittance reports the billed code whenever the payer changes it, and the EMA charge export holds the rest) and the CMS fee schedule files (public).</div></details>`;
  // the checklist and the feeds come from the brief and the sync status: they never wait for EMA's figures
  const fixed = Promise.all([dataChecks(v.querySelector('#checks')), feedPanel(v.querySelector('#feedPanel'))]);
  const gaps = covP.then(cov => { const el = v.querySelector('#dtGaps'); if(el) el.innerHTML = dtGapsHtml(cov || {gaps: []}); });
  const emaDone = emaP.then(async ema => {
    ema = ema || {ready: false};
    const el = v.querySelector('#dtEma'); if(!el) return;
    el.innerHTML = dtEmaHtml(ema);
    dtBind(v, ema);
    if(typeof tdBlindBanner === 'function') await tdBlindBanner(v.querySelector('#dtBlind'), ema.ready ? ema.blind : undefined);
    if(anchor) await dtLand(v, anchor, ema);
  });
  await Promise.all([fixed, gaps, emaDone]);
}

// a link from another page names what it is about: its list opens, and the page scrolls to it
async function dtLand(v, anchor, ema){
  const key = {dtPostList: 'post', dtRefundList: 'refund'}[anchor];
  if(key) await dtItemsOpen(v, key, ema);
  const el = v.querySelector('#' + anchor);
  if(el && el.scrollIntoView) el.scrollIntoView({block: 'start'});
}

function dtGapsHtml(cov){
  cov = cov || {gaps: []};
  const gaps = (cov.gaps||[]).length ? `<div class="panel"><h2>Remittance weeks that look missing</h2>
    <div class="note">A payer that pays every week sent next to nothing in these weeks and then started again, and the visits those weeks would have paid are still short. Either its remittance files for those weeks never reached the Dropbox folder or it paid nothing. Ask TriZetto to send the files again.</div>
    <div class="scroll"><table><thead><tr><th>Payer</th><th>Payer ID</th><th>From</th><th>To</th><th class="num">Weeks</th><th class="num">Claims received</th><th class="num">Usual for those weeks</th><th>Visits still short, about</th></tr></thead>
    <tbody>${cov.gaps.map(g=>`<tr><td>${esc(g.payer)}</td><td class="small muted">${esc(g.payer_id)}</td><td>${dtDay(g.from)}</td><td>${dtDay(g.to)}</td><td class="num">${esc(g.weeks)}</td><td class="num">${Number(g.seen||0).toLocaleString()}</td><td class="num">${Number(g.usual||0).toLocaleString()}</td>
      <td>${Object.entries(g.missing_by_month||{}).map(([mo,n])=>`${Number(n).toLocaleString()} claims for ${esc(rwMonth(mo))}`).join(', ')}</td></tr>`).join('')}</tbody></table></div></div>` : '';
  const ew = cov.ema || {};
  const thin = (ew.weeks||[]).filter(w => w.claims >= (ew.min_claims||30) && w.in_ema < (ew.share||0.8) * w.claims);
  const emaGaps = thin.length ? `<div class="panel"><h2>EMA charge exports that miss visits${ew.measured_at?` <span class="small muted" style="font-weight:400">checked ${dtDay(ew.measured_at)}</span>`:''}</h2>
    <div class="note">For these weeks of service the remittances hold claims that no Encounter &amp; Charge Lines export holds. Pull Encounter &amp; Charge Lines again for these dates of service, in the layout with Patient ID.</div>
    <div class="scroll"><table><thead><tr><th>Week of service</th><th class="num">Claims on remittances</th><th class="num">Found in an EMA charge export</th><th class="num">Share</th></tr></thead>
    <tbody>${thin.map(w=>`<tr><td>${dtDay(w.week)}</td><td class="num">${Number(w.claims).toLocaleString()}</td><td class="num">${Number(w.in_ema).toLocaleString()}</td><td class="num">${Math.round(100*w.in_ema/w.claims)}%</td></tr>`).join('')}</tbody></table></div></div>` : '';
  return gaps + emaGaps;
}

// ---------------------------------------------------------------- EMA's reports and what they show
function dtEmaHtml(e){
  if(!e || !e.ready){
    return `<div class="panel"><h2>EMA's reports</h2><div class="note">${e && e.error ? 'The EMA figures did not come back: ' + esc(e.error) + '. Reload in a minute.'
      : 'No A/R or Production report from EMA has been read yet. The <a href="/upload">Upload page</a> lists what to pull and how.'}</div></div>`;
  }
  return [dtReportsHtml(e), dtMissingHtml(e), dtUnpostedHtml(e), dtCreditsHtml(e), dtArHtml(e), dtPolicyHtml(e), dtRunHtml(e)].join('');
}

function dtFileRow(x){
  const st = DT_STATUS[x.status] || [x.status, ''];
  if(dtNarrow()){
    const rows = x.rows_applied != null ? x.rows_applied : x.rows_read;
    return `<div class="check"><div><b>${esc(DT_REPORT[x.report] || x.report || '')}</b> <span class="small muted">${esc(x.source_name||'')}</span>
      <div class="small">${[dtScope(x), x.as_of ? 'as of ' + dtDay(x.as_of) : '', rows != null ? pl(rows, 'row') : ''].filter(Boolean).join(' · ')}</div>
      <div class="small"><span class="${st[1]}">${esc(st[0])}</span>${x.decided_by ? ` · ${esc(x.decision === 'discard' ? 'Discarded' : 'Apply chosen')} by ${esc(x.decided_by)} on ${dtDay(x.decided_at)}` : ''} · ${esc(x.from||'')}</div></div></div>`;
  }
  const rows = x.rows_applied != null ? Number(x.rows_applied).toLocaleString() : (x.rows_read != null ? Number(x.rows_read).toLocaleString() : '');
  const repl = (x.replaced||[]).length ? `<div class="small muted">replaced ${esc(pl(x.replaced.length, 'earlier file'))}</div>` : '';
  return `<tr><td>${esc(DT_REPORT[x.report] || x.report || '')}<div class="small muted">${esc(x.source_name||'')}</div></td><td class="small">${dtScope(x)}${x.aging_window===30?'<div class="sev-medium">0 to 30 days only</div>':''}</td>
    <td class="small">${dtDay(x.as_of)}</td><td class="num">${rows}</td><td class="small"><span class="${st[1]}">${esc(st[0])}</span>${repl}${x.status_reason && !['current','partly_replaced'].includes(x.status) ? `<div class="small muted">${esc(x.status_reason)}</div>` : ''}${x.decided_by ? `<div class="small muted">${esc(x.decision === 'discard' ? 'Discarded' : 'Apply chosen')} by ${esc(x.decided_by)} on ${dtDay(x.decided_at)}</div>` : ''}</td>
    <td class="small">${esc(x.from||'')}</td></tr>`;
}

function dtReportsHtml(e){
  const f = e.fresh || {}, cat = e.catalog || {}, files = cat.files || [];
  if(cat.error) return `<div class="panel"><h2>EMA's reports</h2><div class="note sev-high">The list of reports did not come back (${esc(cat.error)}).</div></div>`;
  const held = files.filter(x=>x.status === 'held'), waiting = files.filter(x=>['waiting','reread'].includes(x.status));
  const line = [f.ar_as_of ? `A/R as of <b>${dtDay(f.ar_as_of)}</b> (${dtAge(f.ar_days, 8)})` : 'No A/R report yet',
    f.production_as_of ? `Production through <b>${dtDay(f.production_as_of)}</b> (${dtAge(f.production_days, 14)})` : 'No Production report yet',
    f.newest_835 ? `newest remittance ${dtDay(f.newest_835)}` : ''].filter(Boolean).join(' · ');
  const heldHtml = held.map(dtHeldHtml).join('');
  const nr = cat.not_read || [];
  return `<div class="panel" id="dtReports"><div class="panel-head"><h2>EMA's reports</h2><span class="small muted">${line}</span></div>
    ${heldHtml}
    ${waiting.length ? `<div class="small">${esc(pl(waiting.length, 'report'))} ${waiting.length===1?'waits':'wait'} to be read: ${waiting.map(x=>esc(x.status_reason || 'the next run reads it')).filter((t,i,a)=>a.indexOf(t)===i).join(' ')}</div>` : ''}
    <details class="dfold" ${held.length ? 'open' : ''}><summary>${esc(pl(files.length, 'report file'))} on file</summary>
    ${dtNarrow() ? `<div id="dtFiles">${files.slice(0, DT_SHOW).map(dtFileRow).join('')}</div>`
      : `<div class="scroll"><table class="small"><thead><tr><th>Report</th><th>Covers</th><th>As of</th><th class="num">Rows</th><th>Status</th><th>From</th></tr></thead>
    <tbody id="dtFiles">${files.slice(0, DT_SHOW).map(dtFileRow).join('')}</tbody></table></div>`}
    ${files.length > DT_SHOW ? `<button class="ghost" id="dtAllFiles">Show all ${files.length}</button>` : ''}</details>
    ${nr.length ? `<div class="small muted" style="margin-top:6px">${esc(pl(nr.length, 'file'))} the site did not read: not one of EMA's reports or weekly exports, or too big for this site${nr[0].ingested_at ? ` (the newest ${dtDay(nr[0].ingested_at)})` : ''}.</div>` : ''}
    ${(cat.misread_suspects||[]).length ? `<div class="small sev-medium">${esc(pl(cat.misread_suspects.length, 'earlier file'))} may be a report read as a weekly export; the maintainer can check.</div>` : ''}
    <div class="small" style="margin-top:6px"><a href="/upload">Upload a report</a> · the Upload page lists the five exports, what each is for and how to pull it.</div></div>`;
}

// a held report: what held it, and Apply or Discard. Applying reads it as it is, so it is never the obvious button and it
// asks first. A choice made since the newest run waits for the next run; one made before it and still held was not read
// by that run, and is offered again
function dtHeldHtml(x){
  const name = DT_REPORT[x.report] || 'A report', file = esc(x.file_id);
  const chose = x.decided_by ? `${x.decision === 'discard' ? 'Discard' : 'Apply'} chosen by ${esc(x.decided_by)} on ${dtDay(x.decided_at)}` : '';
  const pending = !!x.decision_pending;
  const said = chose ? `<div class="small">${chose}${pending ? '; the next run reads it.' : ', and it is still held: choose again.'}</div>` : '';
  const btns = pending ? `<button data-decide="discard" data-file="${file}">Discard it instead</button>`
    : `<button data-decide="apply" data-file="${file}">${chose ? 'Apply it again' : 'Apply it'}</button><button data-decide="discard" data-file="${file}">Discard it</button>`;
  return `<div class="warnbox" data-held="${file}"><b>${esc(name)} held${x.source_name ? ': ' + esc(x.source_name) : ''}.</b> ${esc(x.status_reason || 'It needs a decision before it is read.')}
      ${said}<div class="actions" style="margin-top:6px">${btns}<span class="small muted dtDecideMsg"></span></div></div>`;
}

function dtMissingHtml(e){
  const r = e.reconcile || {}, c1 = r.c1 || {}, rows = Array.isArray(e.resend) ? e.resend : [];
  if(r.error) return `<div class="panel"><h2>Payments with no remittance</h2><div class="note sev-high">These figures did not come back (${esc(r.error)}).</div></div>`;
  if(!c1.causes && !rows.length) return '';
  const words = e.cause_words || {};
  const causes = Object.entries(c1.causes || {}).sort((a,b)=>(b[1].cents||0)-(a[1].cents||0));
  const since = r.since_feed_stopped || {};
  const bs = e.basis || {};
  return `<div class="panel" id="dtMissing"><div class="panel-head"><h2>Payments with no remittance</h2><span class="small muted">EMA posted <span title="${esc(bs.c1 || '')}">${money(c1.unmatched_cents)}</span> of insurance payments that no remittance on file carries. Without the remittance nobody can see what the payer denied, cut or wrote off on those claims.</span></div>
    ${since.cents ? `<div class="small"><b>${money(since.cents)}</b> of it was posted after the newest remittance (${esc(pl(since.checks, 'check'))}). ${esc((words.after_feed || {}).ask || 'The remittance feed is behind.')} Those checks match by themselves once their remittances are in, so they are not among the requests below.</div>` : ''}
    ${rows.length ? `<h3 class="small" style="margin:10px 0 4px">Remittances to ask for</h3>
      <div class="batches" id="dtAskList">${rows.slice(0, DT_ASK_SHOW).map(dtAskRow).join('')}</div>
      ${rows.length > DT_ASK_SHOW ? `<button class="ghost" id="dtAllAsk">Show all ${rows.length.toLocaleString()} requests</button>` : ''}
      <textarea id="dtAskText" class="small" rows="5" style="width:100%;margin-top:6px" hidden readonly aria-label="The request text"></textarea>` : ''}
    <details class="dfold"><summary>By cause</summary><table class="small"><thead><tr><th>Cause</th><th class="num">Checks</th><th class="num">Dollars</th></tr></thead>
      <tbody>${causes.map(([k,x])=>`<tr><td>${esc((words[k]||{}).why || k)}</td><td class="num">${Number(x.checks||0).toLocaleString()}</td><td class="num">${money(x.cents)}</td></tr>`).join('')}</tbody></table>
      ${c1.no_check_cents ? `<div class="small muted">Also ${money(c1.no_check_cents)} posted as insurance payments with no check number at all: no remittance can be matched to those.</div>` : ''}</details>
    <div class="small" style="margin-top:6px"><a href="/api/ema/checks.csv" download>Download every check with no remittance (CSV)</a></div></div>`;
}

// one payer's checks of one month that came with no remittance, and the button that copies what to ask for
function dtAskRow(x, i){
  return `<div class="batch"><div class="b-main"><b>${esc(x.payer_group)}</b><div class="small">${esc(x.why)}</div><div class="small muted">${esc(x.ask)}</div></div>
    <div class="b-num small">posted in<div><b>${esc(rwMonth(x.month))}</b></div></div><div class="b-due small">${esc(pl(x.checks, 'check'))}</div>
    <div class="b-prog"><b>${money2(x.cents)}</b></div><button class="small dtAsk" data-i="${i}">Copy the request</button></div>`;
}

function dtUnpostedHtml(e){
  const c4 = (e.reconcile || {}).c4 || {}, it = (e.items || {}).post || {}, bs = e.basis || {};
  // the run's list also holds payments a remittance reported whose money never arrived (ask the payer for them): every row
  // of the file it links is counted here
  if(!(c4.n || c4.short_n || c4.unfunded_n || it.n)) return '';
  return `<div class="panel" id="dtUnposted"><div class="panel-head"><h2>Payments the payer sent that EMA never posted</h2><span class="small muted">The payer paid and sent the remittance, and EMA has no posting for it: the money is not on the patient's account.</span></div>
    ${c4.n || c4.short_n || c4.unfunded_n ? `<div class="small" title="${esc(bs.c4 || '')}"><b>${esc(pl(c4.n, 'payment'))}</b> not posted, ${money(c4.cents)}${c4.short_n ? ` · <b>${esc(pl(c4.short_n, 'payment'))}</b> posted short, ${money(c4.short_cents)}` : ''}${c4.unfunded_n ? ` · <b>${esc(pl(c4.unfunded_n, 'payment'))}</b> the payer reported but never sent, ${money(c4.unfunded_cents)} (ask the payer for ${c4.unfunded_n === 1 ? 'it' : 'them'})` : ''}${c4.as_of ? ` · Production through ${dtDay(c4.as_of)}` : ''}. <a href="/api/ema/unposted.csv" download>Download the list (CSV)</a></div>` : ''}
    ${dtItemsHtml('post', it)}</div>`;
}

// the run's items of one kind (to post, to refund): a count line that is Today's number, and the list on request (open
// when a link landed here). A row opens its item; the item's Next walks the list
const DT_ITEMS = {post: {id: 'dtPostList', one: 'payment', what: 'to post in EMA', sort: 'paid', how: 'Each one is an item that says what to post: open it, post the payment in EMA, then mark it posted.'},
  refund: {id: 'dtRefundList', one: 'credit', what: 'to refund or take back', sort: 'deadline', how: "Each one is an item that says who overpaid and what to send back; Medicare's 60-day ones come first."}};
function dtItemsHtml(key, it){
  const d = DT_ITEMS[key];
  if(!it || it.error) return it && it.error ? `<div class="small sev-high">These items did not come back (${esc(it.error)}).</div>` : '';
  if(!it.n) return '';
  return `<div id="${d.id}" style="margin-top:6px"><div class="small"><b>${esc(pl(it.n, d.one))} ${esc(d.what)}</b>, ${money(it.cents)}. ${esc(d.how)}
      <a href="#" class="dtItemsGo" data-items="${key}">List ${it.n === 1 ? 'it' : 'them'}</a></div><div class="dtItemsBody"></div></div>`;
}
async function dtItemsOpen(v, key, ema, page){
  const d = DT_ITEMS[key], box = v.querySelector('#' + d.id), go = (((ema || {}).items || {})[key] || {}).go;
  if(!box || !go) return;
  const body = box.querySelector('.dtItemsBody'); if(!body) return;
  const size = 25; page = page || 1;
  body.innerHTML = '<div class="muted small">loading…</div>';
  const url = off => '/api/findings?' + qs(Object.assign({live: 1, limit: size, offset: off, sort: d.sort}, go));
  let r; try { r = await api(url((page - 1) * size)); } catch(err){ body.innerHTML = `<div class="small sev-high">The items did not come back: ${esc(err.message || 'no answer from the site')}.</div>`; return; }
  const items = r.items || [], pages = Math.max(1, Math.ceil((r.total || 0) / size));
  const due = x => x.appeal_deadline ? `${key === 'refund' ? 'send by' : 'by'} ${longDate(x.appeal_deadline)}` : '';
  const when = x => longDate(key === 'post' ? (x.payment_date || x.service_date) : (x.service_date || x.payment_date));
  body.innerHTML = `<div class="small" style="margin:6px 0 2px">${esc(pl(r.total, d.one))} ${esc(d.what)}, ${money(r.shortfall_cents)}</div>
    ${dtNarrow() ? `<div>${items.map(x=>`<div class="row check" tabindex="0" role="button" data-id="${esc(x.finding_id)}"><div><b>${money2(x.shortfall_cents)}</b> · ${esc(x.payer_group||'')} · ${esc(when(x))}
        <div class="small">${esc(x.title||'')}</div><div class="small muted">${esc(DT_STATE_WORDS[x.work_state] || '')}${due(x) ? ' · ' + esc(due(x)) : ''}</div></div></div>`).join('')}</div>`
      : `<div class="scroll"><table class="small"><thead><tr><th>Payer</th><th>${key === 'post' ? 'Paid on' : 'Service'}</th><th class="num">Amount</th><th>What</th><th>${key === 'refund' ? 'Send by' : 'Date'}</th><th>State</th></tr></thead>
    <tbody>${items.map(x=>`<tr class="row" tabindex="0" data-id="${esc(x.finding_id)}"><td>${esc(x.payer_group||'')}</td><td class="day">${esc(when(x))}</td><td class="num">${money2(x.shortfall_cents)}</td><td>${esc(x.title||'')}</td><td>${esc(x.appeal_deadline ? longDate(x.appeal_deadline) : '')}</td><td>${esc(DT_STATE_WORDS[x.work_state] || '')}</td></tr>`).join('') || '<tr><td colspan="6" class="muted">None.</td></tr>'}</tbody></table></div>`}
    ${pages > 1 ? `<div class="pager"><button class="dtItemsPrev" ${page<=1?'disabled':''}>‹ prev</button><span>page ${page} of ${pages}</span><button class="dtItemsNext" ${page>=pages?'disabled':''}>next ›</button></div>` : ''}`;
  const ids = items.map(x => x.finding_id), wl = 'data:' + key;
  body.querySelectorAll('.row[data-id]').forEach(tr => { const open = () => {
      // the item's Next and "Mark sent and open the next" walk this list, page after page
      state.worklist = ids.slice(); state.wlTotal = r.total; state.wlKey = wl;
      state.wlNext = async () => { if(state.wlKey !== wl) return 0; const more = await api(url((page - 1) * size + (state.worklist || []).length));
        if(state.wlKey !== wl) return 0; const seen = new Set(state.worklist || []); const fresh = (more.items || []).map(x => x.finding_id).filter(x => !seen.has(x));
        state.worklist = (state.worklist || []).concat(fresh); return fresh.length; };
      if(typeof openDetail === 'function') openDetail(tr.dataset.id); };
    tr.onclick = open; tr.onkeydown = e => { if(e.key === 'Enter' || e.key === ' '){ e.preventDefault(); open(); } }; });
  const pv = body.querySelector('.dtItemsPrev'), nx = body.querySelector('.dtItemsNext');
  if(pv) pv.onclick = () => dtItemsOpen(v, key, ema, page - 1);
  if(nx) nx.onclick = () => dtItemsOpen(v, key, ema, page + 1);
}
const DT_STATE_WORDS = {work_now: 'Work now', waiting: 'Waiting', done: 'Done', accepted: 'Accepted', expired: 'Expired', cant_check: "Can't be checked"};

function dtCreditsHtml(e){
  const c8 = (e.reconcile || {}).c8 || {}, it = (e.items || {}).refund || {}, bs = e.basis || {};
  const mp = c8.medicare_primary_over60 || {}, oth = c8.other_over60 || {}, ins = c8.insurance || {}, pat = c8.patient || {};
  if(!(ins.n || pat.n || it.n)) return '';
  return `<div class="panel" id="dtCredits"><div class="panel-head"><h2>Credits</h2><span class="small muted">Money a payer or a patient paid beyond what the line owed.</span></div>
    ${dtItemsHtml('refund', it)}
    ${mp.n ? `<div class="warnbox"><b>Compliance: ${esc(pl(mp.n, 'Medicare primary credit'))} over 60 days on EMA's A/R, ${money(-mp.cents)}.</b> Medicare expects an overpayment back within 60 days of finding it. A person decides each one with the compliance lead.</div>` : ''}
    ${ins.n || pat.n ? `<div class="small" title="${esc(bs.c8 || '')}">On EMA's A/R of the newest pull: ${esc(pl(ins.n, 'insurance credit'))}, ${money(-(ins.cents||0))} · ${esc(pl(pat.n, 'patient credit'))}, ${money(-(pat.cents||0))}${oth.n ? ` · ${esc(pl(oth.n, 'other credit'))} over 60 days, ${money(-oth.cents)}` : ''}. <a href="/api/ema/credits.csv" download>Download the ${esc(pl((ins.n||0)+(pat.n||0), 'credit'))} on the A/R (CSV)</a></div>` : ''}</div>`;
}

function dtArHtml(e){
  const w4 = (e.reconcile || {}).w4;
  if(!w4 || !w4.total) return '';
  const order = ['waiting','chase','paid_elsewhere','denied_todo','denied_started','denied_sent','denied_worked','denied_no_item','not_sent','settled','facility','unbilled','other','credits'];
  const labels = w4.labels || {}, b = w4.buckets || {};
  const rows = order.filter(k => (b[k]||{}).n);
  const nin = w4.not_in_export || {};
  return `<div class="panel" id="dtAr"><div class="panel-head"><h2>The A/R accounted for</h2><span class="small muted" title="${esc((e.basis || {}).w4 || '')}">Every insurance line on the A/R of ${dtDay(w4.as_of)}, in one place each. Click a line to list its rows.</span></div>
    <div class="scroll"><table class="small"><thead><tr><th>Where it stands</th><th class="num">Rows</th><th class="num">Dollars</th></tr></thead>
    <tbody>${rows.map(k=>`<tr class="row dtBucket" tabindex="0" data-bucket="${esc(k)}"><td>${esc(labels[k] || k)}</td><td class="num">${Number(b[k].n).toLocaleString()}</td><td class="num">${money(b[k].cents)}</td></tr>`).join('')}
      <tr><td><b>Total</b> ${w4.adds_up ? '<span class="ok small">adds up</span>' : '<span class="sev-high small">does not add up: tell the maintainer</span>'}</td><td class="num"><b>${Number(w4.total.n||0).toLocaleString()}</b></td><td class="num"><b>${money(w4.total.cents)}</b></td></tr></tbody></table></div>
    ${nin.n ? `<div class="small muted">${esc(nin.label || 'Not in this export')}: ${esc(pl(nin.n, 'line'))}, ${money(nin.cents)} (not in the total).</div>` : ''}
    <div id="dtArRows"></div></div>`;
}

function dtPolicyHtml(e){
  const p = e.policy || {}, ub = e.upper_bound || {}, dc = p.decide || {}, c7 = p.contractual || {}, bs = e.basis || {};
  if(p.error) return `<div class="panel" id="dtPolicy"><div class="panel-head"><h2>Denials written off in EMA</h2></div><div class="note sev-high">These figures did not come back (${esc(p.error)}).</div></div>`;
  if(!p.patterns && !ub.n) return '';
  return `<div class="panel" id="dtPolicy"><div class="panel-head"><h2>Denials written off in EMA</h2></div>
    ${p.patterns ? `<div class="small">The write-off policy page lists <b>${esc(pl(p.patterns, 'pattern'))}</b> (payer, code, reason) for the owner to decide.
      ${p.cents || p.appealable_cents ? `Denials written off in EMA, by its posting rules or later by a person: <span title="${esc(bs.policy || '')}">${money(p.cents)} in all, ${money(p.appealable_cents)} still appealable (estimates)</span>${p.legal_only_cents ? `, of it ${money(p.legal_only_cents)} only inside the payer's own limit (one at a time)` : ''}.` : ''}
      ${dc.patterns ? `<b>${esc(pl(dc.patterns, 'code'))} a payer almost never pays</b> (${esc(pl(dc.items, 'denial'))}, <span title="${esc(bs.payer_policy || '')}">${money(dc.cents)} est.</span>) ${dc.patterns===1?'waits':'wait'} for the owner's decision: out of Work now until then.` : ''}
      ${c7.patterns ? `EMA posts ${esc(pl(c7.patterns, 'payer denial reason'))} as contractual: <span title="${esc(bs.contractual || '')}">${money(c7.written_off_cents)}</span> written off that way, in no total.` : ''}
      ${p.accepted ? esc(pl(p.accepted, 'pattern')) + ' accepted so far. ' : ''}<a href="#policy" class="dtGo" data-tab="policy">Decide them on the write-off policy page</a>.</div>` : ''}
    ${ub.n ? `<div class="small muted" style="margin-top:4px">${esc(pl(ub.n, 'open item'))} ${ub.n===1?'has':'have'} no price, because no rate covers the line. EMA wrote off up to ${money(ub.cents)} on them (the charge less what was paid): an upper bound, left out of every total.</div>` : ''}</div>`;
}

function dtRunHtml(e){
  const w = e.wiring || {}, errs = w.errors || [];
  if(!errs.length) return '';
  return `<div class="panel" id="dtRun"><h2>What the last run noted</h2><div class="note">The run on ${dtDay(w.at)} could not finish ${esc(pl(errs.length, 'EMA step'))}. The items from the remittances were produced as usual; the figures of ${errs.length===1?'that step':'those steps'} wait for the next run.</div>
    <ul class="small">${errs.map(x=>`<li>${esc(x.step)}: ${esc(x.error)}</li>`).join('')}</ul><div class="small muted">Tell RemitWatch's maintainer if it shows again after the next run.</div></div>`;
}

function dtBind(v, ema){
  v.querySelectorAll('.dtGo').forEach(a=>a.onclick=e=>{ e.preventDefault(); state.tab = a.dataset.tab; location.hash = a.dataset.tab; render(); });
  const all = v.querySelector('#dtAllFiles');
  if(all) all.onclick = () => { v.querySelector('#dtFiles').innerHTML = ((ema.catalog||{}).files || []).map(dtFileRow).join(''); all.remove(); };
  const files = (ema.catalog || {}).files || [];
  v.querySelectorAll('[data-decide]').forEach(b=>b.onclick=async()=>{
    const box = b.closest('.warnbox'), msg = box && box.querySelector('.dtDecideMsg'), what = b.dataset.decide;
    const x = files.find(f => f.file_id === b.dataset.file) || {};
    if(what === 'discard' && !confirm('Discard this report? It is not read, and nothing it holds reaches the site.')) return;
    if(what === 'apply' && !confirm(dtApplyWords(x))) return;
    if(box) box.querySelectorAll('button').forEach(x=>x.disabled = true);
    try { await api('/api/ema/reports/'+encodeURIComponent(b.dataset.file)+'/decision', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({decision: what})});
      if(msg) msg.textContent = what === 'apply' ? 'Saved: the next run reads it.' : 'Saved: discarded.'; setTimeout(()=>render(), 1200); }
    catch(err){ if(box) box.querySelectorAll('button').forEach(x=>x.disabled = false); if(msg) msg.textContent = 'Not saved: ' + (err.message || 'no answer from the site') + '.'; }
  });
  v.querySelectorAll('.dtItemsGo').forEach(a=>a.onclick=e=>{ if(e) e.preventDefault(); dtItemsOpen(v, a.dataset.items, ema); });
  const rows = Array.isArray(ema.resend) ? ema.resend : [];
  const bindAsk = () => v.querySelectorAll('.dtAsk').forEach(b=>b.onclick=async()=>{
    const x = rows[Number(b.dataset.i)]; if(!x) return;
    b.disabled = true;
    try { const text = await dtRequestText(x); const ta = v.querySelector('#dtAskText'); if(ta){ ta.hidden = false; ta.value = text; } dtCopy(text, b); }
    catch(err){ alert('The checks did not come back: ' + (err.message || 'no answer from the site')); }
    finally { b.disabled = false; }
  });
  bindAsk();
  const allAsk = v.querySelector('#dtAllAsk');
  if(allAsk) allAsk.onclick = () => { v.querySelector('#dtAskList').innerHTML = rows.map(dtAskRow).join(''); allAsk.remove(); bindAsk(); };
  v.querySelectorAll('.dtBucket').forEach(tr=>{ const go = () => dtArRows(v, tr.dataset.bucket, 1, ema);
    tr.onclick = go; tr.onkeydown = e=>{ if(e.key==='Enter'||e.key===' '){ e.preventDefault(); go(); } }; });
}

// what Applying a held report does, said before it is done: it is read as it is, and replaces what is on file for the
// same report and dates; the safe way is to pull it again
function dtApplyWords(x){
  const name = DT_REPORT[x.report] || 'report', scope = dtScope(x).replace(/<[^>]*>/g, '');
  return `Apply this ${name}${x.source_name ? ' (' + x.source_name + ')' : ''} as it is? The next run reads it and it replaces what is on file for `
    + (scope ? `the same ${x.report === 'ar_report' ? 'year' : 'dates'} (${scope})` : `the same ${x.report === 'ar_report' ? 'year' : 'dates'}`)
    + `.${x.status_reason ? '\n\nIt was held because: ' + x.status_reason : ''}\n\nThe safe way is to pull it again from EMA with no filter and upload that; then discard this one.`;
}

// the facts TriZetto or the payer needs to send remittances again: payer, month, and each check (number, date, amount)
async function dtRequestText(x){
  const lines = [];
  let page = 1, got;
  do {
    got = await api('/api/ema/checks?'+qs({payer: x.payer_group, month: x.month, cause: x.cause, page}));
    (got.rows||[]).forEach(r=>lines.push(`- check ${r.check_number}, posted ${r.first_posted}, ${r.payer}, ${money2(r.cents)}`));
    page += 1;
  } while((got.rows||[]).length && lines.length < got.count && page < 40);
  const one = lines.length === 1;
  const head = x.cause === 'no_era' || x.cause === 'no_era_va'
    ? `Please set up electronic remittances (835) for ${x.payer_group}. ${one ? 'This payment' : `These ${lines.length} payments`} posted in ${rwMonth(x.month)} came with no remittance:`
    : `Please send again the 835 ${one ? 'remittance for this check' : `remittances for these ${lines.length} checks`} from ${x.payer_group}, posted in ${rwMonth(x.month)} (${money2(x.cents)} in all):`;
  return head + '\n' + lines.join('\n');
}

async function dtArRows(v, bucket, page, ema){
  const el = v.querySelector('#dtArRows'); if(!el) return;
  el.innerHTML = '<div class="muted small">loading…</div>';
  let r; try { r = await api('/api/ema/ar?'+qs({bucket, page})); } catch(err){ el.innerHTML = `<div class="small sev-high">The rows did not come back: ${esc(err.message)}.</div>`; return; }
  const url = r.claim_url, link = n => (url && /^\d{6,12}$/.test(String(n||''))) ? `<a href="${esc(url.replace('{claim}', String(n)))}" target="_blank" rel="noopener noreferrer">Open in EMA</a>` : '';
  const item = x => x.finding_id ? `<a href="#" class="dtArItem" data-id="${esc(x.finding_id)}">Open the item</a>` : '';
  const pages = Math.max(1, Math.ceil((r.count||0) / (r.size||100)));
  const rows = r.rows || [];
  // a phone: one card per row, its actions first, so they are on screen
  const body = dtNarrow() ? `<div>${rows.map(x=>`<div class="check"><div><div>${[link(x.link), item(x)].filter(Boolean).join(' · ')}</div>
      <div class="small"><b>${esc(x.claim_id||'')}</b> · ${esc(x.payer_group||'')} · ${money2(x.balance_cents)}</div>
      <div class="small muted">${[dtDay(x.service_date), esc(x.code||''), esc(x.status||''), x.days==null ? '' : esc(pl(x.days, 'day'))].filter(Boolean).join(' · ')}${x.has_note?' <span class="chip">note</span>':''}</div></div></div>`).join('') || '<div class="muted small">No rows.</div>'}</div>`
    : `<div class="scroll"><table class="small"><thead><tr><th>Claim</th><th>Payer</th><th>Service</th><th>Code</th><th class="num">Balance</th><th>Status</th><th class="num">Days</th><th></th></tr></thead>
    <tbody>${rows.map(x=>`<tr><td>${esc(x.claim_id||'')}</td><td>${esc(x.payer_group||'')}</td><td>${dtDay(x.service_date)}</td><td>${esc(x.code||'')}</td><td class="num">${money2(x.balance_cents)}</td><td>${esc(x.status||'')}${x.has_note?' <span class="chip">note</span>':''}</td><td class="num">${x.days==null?'':Number(x.days)}</td><td>${[link(x.link), item(x)].filter(Boolean).join(' · ')}</td></tr>`).join('') || '<tr><td colspan="8" class="muted">No rows.</td></tr>'}</tbody></table></div>`;
  el.innerHTML = `<h3 class="small" style="margin:10px 0 4px" title="${esc(((ema || {}).basis || {}).w4 || '')}">${esc(r.label)}: ${esc(pl(r.count, 'row'))}, ${money(r.cents)}</h3>
    ${r.pending ? `<div class="small sev-medium">${esc(pl(r.pending, 'row'))} of a newer A/R ${r.pending===1?'is':'are'} not sorted yet: the next run sorts ${r.pending===1?'it':'them'}.</div>` : ''}
    ${body}
    <div class="pager"><button class="dtPrev" ${page<=1?'disabled':''}>‹ prev</button><span>page ${page} of ${pages}</span><button class="dtNext" ${page>=pages?'disabled':''}>next ›</button></div>`;
  el.querySelector('.dtPrev').onclick = () => dtArRows(v, bucket, page-1, ema);
  el.querySelector('.dtNext').onclick = () => dtArRows(v, bucket, page+1, ema);
  el.querySelectorAll('.dtArItem').forEach(a=>a.onclick=e=>{ if(e) e.preventDefault(); if(typeof openDetail === 'function') openDetail(a.dataset.id); });
}
