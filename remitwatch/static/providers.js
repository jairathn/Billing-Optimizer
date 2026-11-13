/* RemitWatch dashboard: what each clinician is doing. Loaded after features.js and before the inline script in index.html;
   uses their helpers ($, api, qs, esc, money, chart, sweepCharts, state, render).

   The three answers the tab is built from are kept in state.pv.data, so a change of filter, metric, theme or picked
   clinician redraws from memory and the reader stays where they were on the page. Only a change of months asks the
   server again, and an answer that is no longer the newest is dropped.

   Charts: one y-axis per chart, a clinician keeps the same treatment whatever the filter, and each chart has its numbers
   in a table under it. The eight colours are a fixed order. Neighbours in a stack can be told apart with the common
   colour-vision deficiencies; pairs further apart cannot always, which is what the tables are for. */

const PV_SERIES = {light: ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#6250d6', '#e34948'],
                   dark: ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#008300', '#9085e9', '#e66767']};
const PV_GROUPS = [['Office visits', ['new_visit', 'est_visit', 'visit_addon']], ['Biopsies', ['biopsy']],
  ['Destruction', ['destruction_premalignant', 'destruction_benign', 'destruction_malignant']],
  ['Excision, repair and Mohs', ['excision', 'repair', 'mohs']], ['Pathology', ['pathology']], ['Radiation', ['radiation']],
  ['Skin substitutes and drugs', ['skin_substitute', 'drug']], ['Other', null]];       // null: every family not named above
const PV_METRICS = {per_day: ['Patients per clinic day', 'median; quarters with fewer than 8 clinic days are left out'], clinic_days: ['Clinic days', ''],
  patients: ['Patients seen', ''], new_visits: ['New patient visits', ''],
  est_alone_high_share: ['Established visits with no procedure, share billed 99214 or 99215', 'quarters with fewer than 30 such visits are left out'],
  est_high_share: ['All established visits, share billed 99214 or 99215', 'quarters with fewer than 30 visits are left out'],
  biopsied_per_100_patients: ['Patients biopsied per 100 seen', 'quarters with fewer than 30 patients are left out'],
  allowed_per_patient: ['Allowed per patient seen', 'quarters with fewer than 30 patients are left out']};
const PAYER_LABEL = {medicare: 'Medicare', medicare_advantage: 'Medicare Advantage', commercial: 'Commercial', medicaid: 'Medicaid', supplement: 'Supplement',
  tricare: 'Tricare', va: 'VA', other: 'Other', 'secondary only': 'Secondary payer only'};
const PV_MONTH = /^[0-9]{4}-(0[1-9]|1[0-2])$/;
const n0 = x => x == null ? '–' : Math.round(x).toLocaleString('en-US');
const n1 = x => x == null ? '–' : Number(x).toFixed(1);
const n2 = x => x == null ? '–' : Number(x).toFixed(2);
const pctv = x => x == null ? '–' : Math.round(100 * x) + '%';
const plural = (n, one, many) => `${n0(n)} ${Math.round(n) === 1 ? one : many}`;
const tierLabel = t => t === 'physician' ? 'MD' : (t === 'nonphysician' ? 'PA / NP' : (t === 'staff' ? 'staff' : ''));
const monthName = m => { if(!PV_MONTH.test(m || '')) return ''; const d = new Date(m + '-15T00:00:00Z'); return d.toLocaleString('en-US', {month: 'short', year: 'numeric', timeZone: 'UTC'}); };
const dayName = (s, year) => { const d = new Date(String(s || '').slice(0, 10) + 'T00:00:00Z'); if(isNaN(d)) return esc(s || '?');
  return d.toLocaleDateString('en-US', Object.assign({month: 'short', day: 'numeric', timeZone: 'UTC'}, year === false ? {} : {year: 'numeric'})); };
const quarterName = q => String(q || '').replace('-', ' ');
const addMonths = (m, k) => { const y = +m.slice(0, 4), i = +m.slice(5, 7) - 1 + k; return String(y + Math.floor(i / 12)).padStart(4, '0') + '-' + String(((i % 12) + 12) % 12 + 1).padStart(2, '0'); };

function pvTheme(){
  const root = document.documentElement, cs = getComputedStyle(root);
  const dark = root.dataset.theme === 'dark' || (root.dataset.theme !== 'light' && window.matchMedia('(prefers-color-scheme: dark)').matches);
  const v = k => cs.getPropertyValue(k).trim();
  return {dark, series: PV_SERIES[dark ? 'dark' : 'light'], ink: v('--ink'), muted: v('--muted'), line: v('--line'), surface: v('--panel'), context: dark ? '#6b7786' : '#9aa5b1'};
}
function pvAxes(t, extra){
  const axis = {grid: {color: t.line, drawTicks: false}, border: {color: t.line}, ticks: {color: t.muted, font: {size: 11}}};
  return {x: Object.assign({}, axis, {grid: {display: false}}, (extra || {}).x), y: Object.assign({beginAtZero: true}, axis, (extra || {}).y)};
}
function pvTable(rows, cols, opts){
  const o = opts || {};
  return `<div class="scroll"><table class="pvt"><thead><tr>${cols.map(c => `<th class="${c.num ? 'num' : ''}"${c.tip ? ` title="${esc(c.tip)}"` : ''}>${c.h}</th>`).join('')}</tr></thead>
    <tbody>${rows.map(r => `<tr${o.pick ? ` class="row${o.picked === r.provider_id ? ' picked' : ''}" data-pick="${esc(r.provider_id)}"` : ''}>${cols.map(c => `<td class="${c.num ? 'num' : ''}">${c.f(r)}</td>`).join('')}</tr>`).join('')
      || `<tr><td colspan="${cols.length}" class="muted">Nobody to show for these months and this filter.</td></tr>`}</tbody></table></div>`;
}
function pvWho(r, sub){
  return `<b>${esc(r.name || r.provider_id)}</b> <span class="chip">${esc(tierLabel(r.tier))}</span>${sub ? `<span class="sub">${sub}</span>` : ''}`;
}
function pvRange(r, max){
  const p = r.per_day || {};
  if(p.median == null || !max) return '';
  const left = 100 * p.p25 / max, width = Math.max(1.5, 100 * (p.p75 - p.p25) / max), mid = 100 * p.median / max;
  return `<div class="rng" title="middle half of clinic days: ${n1(p.p25)} to ${n1(p.p75)}; busiest day ${n0(p.max)}"><i style="left:${left.toFixed(1)}%;width:${width.toFixed(1)}%"></i><b style="left:${mid.toFixed(1)}%"></b></div>`;
}
const pvKeep = (tier, r) => tier === 'all' || r.tier === tier;

/* ------------------------------------------------------------------ the owner's password
   When the site has one, these figures are sent only to a request that carries it. It is typed here, kept in this
   page's memory (never in the browser's storage), sent on the provider calls alone, and forgotten on Lock, on reload
   and after half an hour without use. */
const PV_IDLE_MS = 30 * 60 * 1000;
const pvLocked = () => !!(state.meta && state.meta.providers && state.meta.providers.locked);
function pvForget(){ state.ownerKey = ''; if(state.pv){ state.pv.data = null; state.pv.detail = null; clearTimeout(state.pv.idle); } }
function pvUnlockForm(v, message){
  v.classList.remove('busy');
  v.innerHTML = `<div class="panel" style="max-width:560px"><h2>What each clinician is doing</h2>
    <div class="note">This tab compares named clinicians, so it is kept for the owner. Type the owner's password to open it. It is not saved anywhere: closing or reloading the page, or pressing Lock, closes the tab again, and so does half an hour without use.</div>
    <form id="pvUnlock" class="filters"><input id="pvKey" type="password" autocomplete="off" aria-label="owner's password" placeholder="owner's password" style="min-width:240px"> <button class="primary">Open</button></form>
    ${message ? `<div class="small sev-medium">${esc(message)}</div>` : ''}</div>`;
  v.querySelector('#pvUnlock').onsubmit = e => {
    e.preventDefault();
    const key = v.querySelector('#pvKey').value;
    if(!key) return;
    if(/[^\x20-\x7e]/.test(key)){ pvUnlockForm(v, "That is not the owner's password."); return; }      // a browser cannot send it, and the site would not accept it
    state.ownerKey = key;
    v.classList.add('busy');
    api('/api/activity/unlock').then(() => { if(state.tab === 'providers') render(); },         // tried with one call, so a wrong try counts once
      err => { pvForget(); if(state.tab === 'providers' && v.isConnected) pvUnlockForm(v, err.message); });
  };
  v.querySelector('#pvKey').focus();
}

/* ------------------------------------------------------------------ loading */
async function providersTab(v){           // called by render(): the tab was opened, or the whole page is being redrawn
  const pv = state.pv = Object.assign({from: '', to: '', preset: 'year', tier: 'all', pick: '', metric: 'per_day'}, state.pv || {});
  pv.el = v;
  if(pvLocked() && !state.ownerKey){ pvUnlockForm(v); return; }
  try { await pvLoad(); }
  catch(e){
    if(pvLocked() && [403, 429, 503].includes(e.status)){ pvForget(); pvUnlockForm(v, e.message); return; }
    throw e;
  }
}
async function pvLoad(){
  const pv = state.pv, v = pv.el, seq = pv.seq = (pv.seq || 0) + 1;
  v.classList.add('busy');
  let ans;
  try {
    ans = await Promise.all([api('/api/activity/overview?' + qs({from: pv.from, to: pv.to})), api('/api/activity/trend'), api('/api/activity/recent')]);
  } catch(e){
    if(seq === pv.seq){       // months the server refused must not stay in state and break the tab for good
      v.classList.remove('busy');
      Object.assign(pv, pv.good || {from: '', to: '', preset: 'year'});
    }
    throw e;
  }
  if(seq !== pv.seq || state.tab !== 'providers' || !v.isConnected) return;      // a newer request is on its way, or the reader has left
  v.classList.remove('busy');
  pv.data = {ov: ans[0], tr: ans[1], rc: ans[2]};
  if(ans[0].ready && pv.preset === 'custom'){ pv.from = ans[0].from; pv.to = ans[0].to; }      // what the server answered (a reversed pair is put right there)
  pv.good = {from: pv.from, to: pv.to, preset: pv.preset};
  pvDraw();
}
function pvReload(){
  pvLoad().catch(e => { const v = state.pv.el; console.error(e);
    if(!v || !v.isConnected || state.tab !== 'providers') return;
    if(pvLocked() && [403, 429, 503].includes(e.status)){ pvForget(); pvUnlockForm(v, e.message); return; }
    pvDraw(); v.insertAdjacentHTML('afterbegin', `<div class="warnbox">Those months could not be loaded (${esc(e.message)}). Showing the months loaded before.</div>`); });
}
function pvRedraw(){ return state.tab === 'providers' && pvDraw(); }      // the theme button

/* ------------------------------------------------------------------ the page */
function pvDraw(){
  const pv = state.pv || {}, v = pv.el, data = pv.data;
  if(!v || !v.isConnected || !data) return false;
  const {ov, tr, rc} = data;
  const notReady = [ov, tr, rc].find(a => !a || !a.ready);
  if(notReady !== undefined){
    const a = notReady || {};
    v.innerHTML = `<div class="panel"><h2>Providers</h2><div>${esc(a.message || 'No provider figures yet.')}</div>${a.error ? `<div class="small sev-high">Last attempt failed: ${esc(a.error)}</div>` : ''}</div>`;
    return true;
  }
  const cov = ov.coverage || {}, lag = cov.lag || {}, last = cov.last_complete_month, bar = cov.complete_share || 0.97;
  if(!(cov.months || []).length || !last){
    v.innerHTML = '<div class="panel"><h2>What each clinician is doing</h2><div>No visits yet. This fills in after the first remittances have been analysed.</div></div>';
    return true;
  }
  const keep = r => pvKeep(pv.tier, r);
  const rows = ov.providers.filter(r => r.clinic_days >= 3 && keep(r));
  const minor = ov.providers.filter(r => r.clinic_days < 3 && keep(r) && (r.patients || r.radiation || r.path_only));
  if(pv.pick && !rows.some(r => r.provider_id === pv.pick)) pv.pick = '';        // the filter or the months removed the picked clinician
  const scrollY = window.scrollY, focus = document.activeElement && v.contains(document.activeElement) ? document.activeElement.id : '';

  const ema = rc.ema || {};
  const where = {};
  (rc.providers || []).forEach(p => { if(p.provider_id && p.offices && p.offices.length) where[p.provider_id] = p.offices.map(o => `${esc(o.name)} ${Math.round(100 * o.share)}%`).join(' · '); });
  const edge = r => {         // someone who started or left inside the window is not measured against all of it
    const out = [];
    if(r.first_clinic_day && r.first_clinic_day.slice(0, 7) > ov.from) out.push('first clinic day ' + dayName(r.first_clinic_day));
    if(r.last_clinic_day && r.last_clinic_day.slice(0, 7) < ov.to) out.push('last clinic day ' + dayName(r.last_clinic_day));
    return out.join(', ');
  };
  const sub = r => [where[r.provider_id], edge(r)].filter(Boolean).join(' · ');
  const places = r => (r.places || []).map(p => `<span title="${esc(p.place)}: ${n0(p.clinic_days)} clinic days, mean ${n1(p.mean)}">${esc(p.place)} <b>${n0(p.per_day)}</b></span>`).join(' · ') || '–';
  const maxDay = Math.max(5, ...rows.map(r => (r.per_day || {}).p75 || 0)) * 1.1;
  const inWindow = (cov.months || []).filter(m => m.month >= ov.from && m.month <= ov.to);
  const filling = inWindow.filter(m => m.complete + (m.missing_to_gaps || 0) < bar).map(m => `${monthName(m.month)} about ${Math.round(100 * m.complete)}% in`);
  const short = inWindow.filter(m => (m.missing_to_gaps || 0) >= 0.005);
  const gaps = (cov.gaps || []).filter(g => Object.keys(g.missing_by_month || {}).some(m => m >= ov.from && m <= ov.to));
  const followed = ov.followed || {}, fNew = followed.new_back, fCa = followed.cancer;
  const span = q => q ? `${quarterName(q[0])} to ${quarterName(q[1])}` : 'no finished quarters yet';
  const preset = pv.preset || 'year';

  const volume = pvTable(rows, [
    {h: 'Clinician', f: r => pvWho(r, sub(r))},
    {h: 'Clinic days', num: 1, f: r => n0(r.clinic_days), tip: `days with at least ${n0(ov.clinic_day_min)} patients seen`},
    {h: 'Clinic days a week', num: 1, f: r => n1(r.days_per_week), tip: 'over the weeks between that clinician\'s first and last clinic day in these months'},
    {h: 'Patients a week', num: 1, f: r => n0(r.patients_per_week), tip: 'over the same weeks; a clinician with short days and more of them can match one with long days and fewer'},
    {h: 'Patients seen', num: 1, f: r => n0(r.patients)},
    {h: 'Per clinic day', num: 1, f: r => `<b>${n0((r.per_day || {}).median)}</b>`, tip: 'median over clinic days'},
    {h: `<span class="muted">0 to ${n0(maxDay)} a day</span>`, f: r => pvRange(r, maxDay)},
    {h: 'Per clinic day, by state', num: 1, f: places, tip: 'median on clinic days that had a Medicare claim, by the state of the Medicare contractor that paid it, which follows the office where the visit took place. Days with no Medicare claim carry no state.'},
    {h: 'New patients a day', num: 1, f: r => n1(r.new_per_day)},
    {h: 'New, share of visits', num: 1, f: r => pctv(r.new_share)},
    {h: 'Post-op and nurse contacts', num: 1, f: r => r.other_contacts ? n0(r.other_contacts) : '–', tip: 'patient-days whose only service was a post-operative check, a light-box session or an injection given by staff; not counted as patients seen'},
  ], {pick: true, picked: pv.pick});
  const back = pvTable(rows, [
    {h: 'Clinician', f: r => pvWho(r)},
    {h: 'Panel', num: 1, f: r => n0(r.panel), tip: 'different patients seen in the twelve months ending with the last month chosen'},
    {h: 'Kept from the year before', num: 1, f: r => pctv(r.kept_share), tip: 'of the patients this clinician saw in the twelve months before, the share seen again by anyone in the practice'},
    {h: 'The same, established patients only', num: 1, f: r => `<b>${pctv(r.kept_est_share)}</b>`, tip: 'leaving out patients who were new to this clinician in the earlier year'},
    {h: 'Came back to the same clinician', num: 1, f: r => pctv(r.kept_same_share), tip: 'of the patients this clinician saw in the twelve months before, the share this clinician saw again'},
    {h: 'New patients back within a year †', num: 1, f: r => pctv(r.new_back_365), tip: 'new patients who returned to anyone in the practice within 365 days'},
  ], {pick: true, picked: pv.pick});
  const visits = pvTable(rows, [
    {h: 'Clinician', f: r => pvWho(r)},
    {h: 'Established visits billed 99214 or 99215', num: 1, f: r => pctv(r.est_high_share)},
    {h: 'The same, visits with no procedure', num: 1, f: r => `<b>${pctv(r.est_alone_high_share)}</b>`, tip: 'established patient visits on a day with no biopsy, destruction, excision or injection for that patient'},
    {h: 'New visits billed 99204 or 99205', num: 1, f: r => pctv(r.new_high_share)},
    {h: 'Downcoded per 100 visits', num: 1, tip: 'visits a payer paid at a lower level, per 100 visits billed (count in brackets)', f: r => r.downcoded_per_100_visits == null ? '–' : `${n2(r.downcoded_per_100_visits)} <span class="muted small">(${n0((r.downcoded || {}).n)})</span>`},
    {h: 'Medicare share of patients', num: 1, f: r => pctv(r.medicare_share), tip: 'Medicare and Medicare Advantage'},
    {h: 'Biopsied per 100, Medicare', num: 1, f: r => n1(r.biopsied_per_100_medicare)},
    {h: 'Biopsied per 100, others', num: 1, f: r => n1(r.biopsied_per_100_other)},
    {h: 'Lesions per biopsied patient', num: 1, f: r => n2(r.lesions_per_biopsied_patient)},
    {h: 'Lesions biopsied per clinic day', num: 1, f: r => n1(r.lesions_per_clinic_day)},
    {h: 'Slides read here', num: 1, f: r => pctv(r.path_read_share), tip: 'share of patients biopsied whose slide was billed by this practice the same day'},
    {h: 'Skin cancer treated here per 100 first biopsies ‡', num: 1, f: r => n1(r.cancer_per_100_biopsied)},
  ], {pick: true, picked: pv.pick});
  const dollars = pvTable(rows, [
    {h: 'Clinician', f: r => pvWho(r)},
    {h: 'Allowed per patient seen', num: 1, f: r => `<b>${money(r.allowed_per_patient)}</b>`, tip: 'pathology, radiation, skin substitute products and drugs left out'},
    {h: 'Allowed per clinic day', num: 1, f: r => money(r.allowed_per_clinic_day)},
    {h: 'Allowed in the exam room', num: 1, f: r => money(r.clinic_allowed_cents)},
    {h: 'Pathology read here on their biopsies, per patient', num: 1, tip: 'allowed for slides read in this practice on specimens this clinician took, billed under the reading physician', f: r => money(r.path_generated_per_patient)},
    {h: 'Skin substitutes and drugs', num: 1, f: r => r.product_allowed_cents ? money(r.product_allowed_cents) : '–', tip: 'allowed for the products themselves, which mostly covers what they cost'},
    {h: 'Pathology read', num: 1, f: r => r.path_only ? plural(r.path_only, 'bill', 'bills') : '–'},
    {h: 'Radiation', num: 1, f: r => r.radiation ? plural(r.radiation, 'treatment', 'treatments') : '–'},
    {h: 'Allowed, everything', num: 1, f: r => money(r.allowed_cents)},
    {h: 'Allowed as share of charges', num: 1, f: r => pctv(r.allowed_share_of_charge)},
  ], {pick: true, picked: pv.pick});
  const recall = ov.recall || {};
  const recallRows = (recall.by_provider || []).filter(r => (r.kind === 'person' ? keep(r) : pv.tier === 'all') && (r.overdue_12 > 0 || r.cancer_patients >= 20));

  v.innerHTML = `<div class="panel" style="margin-top:0"><h2>What each clinician is doing</h2>
    <div class="note">Read from the remittances, so it counts visits an insurer has decided. A self-pay or cosmetic visit never reaches a payer, and a payer whose remittances do not arrive here is missing too. A clinic day is a day on which the clinician saw at least ${n0(ov.clinic_day_min)} patients; a half day counts as one. A slide read on its own bill, a radiation treatment, a post-operative check and a visit to the nurse are counted apart from patients seen.</div>
    <div class="filters">
      <label class="small">Visits <select id="pvPreset">
        <option value="year" ${preset === 'year' ? 'selected' : ''}>last 12 complete months</option>
        <option value="quarter" ${preset === 'quarter' ? 'selected' : ''}>last 3 complete months</option>
        <option value="ytd" ${preset === 'ytd' ? 'selected' : ''}>${esc(last.slice(0, 4))}, complete months</option>
        <option value="all" ${preset === 'all' ? 'selected' : ''}>all complete months</option>
        <option value="custom" ${preset === 'custom' ? 'selected' : ''}>chosen months</option></select></label>
      <label class="small">from <input id="pvFrom" type="month" value="${esc(ov.from)}" min="${esc(cov.first_month || '')}" max="${esc(cov.last_month || '')}"></label>
      <label class="small">to <input id="pvTo" type="month" value="${esc(ov.to)}" min="${esc(cov.first_month || '')}" max="${esc(cov.last_month || '')}"></label>
      <label class="small">Show <select id="pvTier"><option value="all" ${pv.tier === 'all' ? 'selected' : ''}>everyone</option><option value="nonphysician" ${pv.tier === 'nonphysician' ? 'selected' : ''}>PAs and NPs</option><option value="physician" ${pv.tier === 'physician' ? 'selected' : ''}>physicians</option></select></label>
      <span class="muted small">${monthName(ov.from)} to ${monthName(ov.to)}</span>
      ${pvLocked() ? '<button id="pvLock" title="forget the owner\'s password on this page and close the tab">Lock</button>' : ''}
    </div>
    <div class="small muted">Half of all visits are paid within ${n0(lag.p50)} days and nine in ten within ${n0(lag.p90)}, so the newest months are still filling in. Months through ${monthName(last)} are at least ${Math.round(100 * bar)}% in.
      ${filling.length ? `<span class="sev-medium">These months are not complete yet and their counts will rise: ${esc(filling.join(', '))}.</span>` : ''}
      ${ov.unassigned_share ? `${pctv(ov.unassigned_share)} of patients seen could not be tied to a clinician because the payer named only the group. They are not spread evenly, so one clinician can be short by more than that.` : ''}</div>
    ${short.length && gaps.length ? `<div class="warnbox"><b>Remittances are missing for some weeks.</b> ${gaps.map(g => `${esc(g.payer)}, ${dayName(g.from)} to ${dayName(g.to)} (${plural(g.seen, 'claim', 'claims')} where about ${n0(g.usual)} are usual)`).join('; ')}.
      Visits in these months are short because of it: ${short.map(m => `${monthName(m.month)} by about ${Math.max(1, Math.round(100 * m.missing_to_gaps))}%`).join(', ')}. A clinician with more of that payer's patients is short by more. The Data tab lists the weeks to ask TriZetto for.</div>` : ''}</div>

    <div class="panel"><h2>How busy</h2><div class="note">Per clinic day is the median, and the bar is the middle half of that clinician's clinic days on one scale. Click a row for that clinician's months, weekdays, offices and codes.</div>${volume}
      ${Object.keys(where).length ? `<div class="small muted" style="margin-top:6px">Under each name: where their patients were seen in the EMA charge exports (visits ${dayName(ema.first_service)} to ${dayName(ema.last_service)}), whatever months are chosen above.</div>` : ''}
      ${minor.length ? `<div class="small muted" style="margin-top:6px">Fewer than three clinic days in these months: ${minor.map(r => `${esc(r.name)} (${[r.patients ? plural(r.patients, 'patient', 'patients') : '', r.radiation ? plural(r.radiation, 'radiation treatment', 'radiation treatments') : '', r.path_only ? plural(r.path_only, 'pathology bill', 'pathology bills') : ''].filter(Boolean).join(', ')})`).join('; ')}.</div>` : ''}</div>
    <div id="pvDetail"></div>

    <div class="panel"><h2>Over time</h2>
      <div class="filters"><label class="small">Show <select id="pvMetric">${Object.keys(PV_METRICS).map(k => `<option value="${k}" ${pv.metric === k ? 'selected' : ''}>${esc(PV_METRICS[k][0])}</option>`).join('')}</select></label>
        <span class="small muted"><span class="key" style="background:var(--pv1)"></span> the clinician &nbsp; <span class="key" style="background:var(--pvc)"></span> median of the others shown</span></div>
      <div class="note">One small chart per clinician, by quarter of service, all on the same scale. Quarters still filling in are left off. <span id="pvSmallNote"></span></div>
      <div class="sm" id="pvSmall"></div><div class="small muted" id="pvSmallLeft" style="margin-top:6px"></div>
      <details class="small" style="margin-top:8px"><summary>The same numbers as a table</summary><div id="pvTrendTable"></div></details></div>

    <div class="panel"><h2>Who comes back</h2><div class="note">Panel is the number of different patients seen in the twelve months to ${monthName((rows.find(r => r.panel_month) || {}).panel_month || ov.to)}. First-time patients return much less often than established ones whoever sees them, so a clinician with many new patients shows a lower "kept" for that reason alone. The column in bold leaves them out. Patients are told apart by name as the payer wrote it, so read these as about right, not exact.</div>${back}
      <div class="small muted" style="margin-top:6px">† Fixed period: new patients first seen ${span(fNew)}, because each needs a year of follow-up. It does not follow the months chosen above.</div></div>

    <div class="panel"><h2>What a visit holds</h2><div class="note">The level of a visit is easiest to compare on visits with no procedure the same day (in bold), because a procedure changes what the visit code may claim. Biopsy rates are shown within Medicare and within everyone else: age drives biopsies, and the payer is the nearest thing to age on a remittance.</div>${visits}
      <div class="small muted" style="margin-top:6px">‡ Fixed period: biopsies taken ${span(fCa)}, whatever months are chosen above. Counts first biopsies only (no biopsy in the four months before and no skin cancer treated in the year before) and asks how many were followed, within 120 days, by a skin cancer treated in this practice (Mohs, a malignant excision or destruction, radiation). It is a floor. Treatment somewhere else never shows here, a surgeon who treats their own patients will show higher than a clinician who refers, and patients biopsied at an office far from the surgeons are treated elsewhere more often. The pathology results are the direct test.</div></div>

    ${recall.cancer_patients ? `<div class="panel"><h2>Patients to call back</h2>
      <div class="note">People who had a skin cancer treated in this practice (Mohs, a malignant excision or destruction, radiation), and how many of them have not been seen for a visit in twelve months, counted under the clinician who saw them last, as of ${dayName(recall.as_of)}. In all, <b>${n0(recall.overdue_12)}</b> of ${n0(recall.cancer_patients)} have not been seen for twelve months and ${n0(recall.overdue_18)} for eighteen. This does not follow the months chosen above. It is a count from the remittances, where a patient is known only by name: some will have moved, died or be followed elsewhere. The list to work from is the recall report in EMA.</div>
      ${pvTable(recallRows, [
        {h: 'Seen last by', f: r => pvWho(r)},
        {h: 'Treated for a skin cancer here', num: 1, f: r => n0(r.cancer_patients)},
        {h: 'Not seen for 12 months', num: 1, f: r => `<b>${n0(r.overdue_12)}</b>`},
        {h: 'Not seen for 18 months', num: 1, f: r => n0(r.overdue_18)},
        {h: 'Share not seen for 12 months', num: 1, f: r => pctv(r.cancer_patients ? r.overdue_12 / r.cancer_patients : null)},
      ])}</div>` : ''}

    <div class="panel"><h2>What it brings in</h2><div class="note">Allowed is what the payer and the patient together owe under the contract. Per patient seen and "in the exam room" leave out pathology, radiation, and skin substitute products and drugs, which are paid mostly to cover their cost. The pathology column is what the slides from that clinician's biopsies were allowed when they were read in this practice, billed under the physician who read them. It depends on where the slides are sent as much as on what is biopsied. It is there to show what a biopsy brings in, not as a basis for pay: ask counsel before tying anyone's pay to pathology or radiation.</div>${dollars}
      <h3 class="small" style="margin:14px 0 4px">Where the allowed dollars come from</h3><div class="chartbox" style="height:${Math.max(160, 34 * rows.length + 70)}px"><canvas id="pvMix"></canvas></div>
      <details class="small" style="margin-top:8px"><summary>The same numbers as a table</summary><div id="pvMixTable"></div></details></div>

    <div class="panel"><h2>Recent weeks, from the EMA charge exports</h2><div id="pvRecent"></div></div>

    <div class="panel"><h2>What this cannot show</h2><ul class="small" style="margin:4px 0 0 18px;padding:0">
      <li>Whether a light day was an empty schedule or patients who did not come. That needs the appointment export from EMA (scheduled, arrived, no-show, cancelled, with clinician and office). Nothing here reads it yet. Send that export and this page can be extended to show how full each schedule was.</li>
      <li>What each clinician costs: support staff, rooms, pay.</li>
      <li>Visits no insurer pays for, before the EMA exports begin, and visits paid by a payer whose remittances do not arrive here.</li>
      <li>What the pathology showed. A biopsy rate says how often, not how well chosen.</li></ul></div>`;

  sweepCharts();
  if(pvLocked()){
    v.querySelector('#pvLock').onclick = () => { pvForget(); render(); };
    clearTimeout(pv.idle);
    pv.idle = setTimeout(() => { pvForget(); if(state.tab === 'providers') render(); }, PV_IDLE_MS);
  }
  const redraw = () => pvDraw();
  v.querySelector('#pvPreset').onchange = e => {
    const p = e.target.value;
    if(p === 'year') Object.assign(pv, {from: '', to: ''});
    else if(p === 'quarter') Object.assign(pv, {from: addMonths(last, -2), to: last});
    else if(p === 'ytd') Object.assign(pv, {from: last.slice(0, 4) + '-01' < cov.first_month ? cov.first_month : last.slice(0, 4) + '-01', to: last});
    else if(p === 'all') Object.assign(pv, {from: cov.first_month, to: last});
    else Object.assign(pv, {from: ov.from, to: ov.to});
    pv.preset = p;
    if(p === 'custom') redraw(); else pvReload();
  };
  const month = (box, key) => {         // a half-typed or out-of-range month is ignored, not sent
    const m = box.value;
    if(!PV_MONTH.test(m) || m < cov.first_month || m > cov.last_month || m === ov[key]) return;
    Object.assign(pv, {from: ov.from, to: ov.to, preset: 'custom'});
    pv[key] = m;
    pvReload();
  };
  v.querySelector('#pvFrom').onchange = e => month(e.target, 'from');
  v.querySelector('#pvTo').onchange = e => month(e.target, 'to');
  v.querySelector('#pvTier').onchange = e => { pv.tier = e.target.value; redraw(); };
  v.querySelector('#pvMetric').onchange = e => { pv.metric = e.target.value; redraw(); };
  v.querySelectorAll('tr.row[data-pick]').forEach(trEl => trEl.onclick = () => {
    pv.pick = pv.pick === trEl.dataset.pick ? '' : trEl.dataset.pick;
    v.querySelectorAll('tr.row[data-pick]').forEach(t => t.classList.toggle('picked', t.dataset.pick === pv.pick));
    pvShowDetail(true);
  });

  const theme = pvTheme();
  v.style.setProperty('--pv1', theme.series[0]);
  v.style.setProperty('--pvc', theme.context);
  pvSmallMultiples(v, tr, rows, pv.metric, theme, bar);
  pvMix(v, rows, theme);
  pvRecent(v.querySelector('#pvRecent'), rc, keep);
  pvShowDetail(false);
  if(focus){ const f = v.querySelector('#' + focus); if(f) f.focus(); }
  window.scrollTo(0, scrollY);
  return true;
}

function pvSmallMultiples(v, tr, rows, metric, theme, bar){
  const order = rows.map(r => r.provider_id);
  const all = tr.providers.filter(p => order.includes(p.provider_id)).sort((a, b) => order.indexOf(a.provider_id) - order.indexOf(b.provider_id));
  // a quarter after the last complete one is still filling in, and one before the first complete one began before the
  // remittances did. A quarter between them that falls short is short because a payer's weeks are missing: it is shown, hollow.
  const done = tr.periods.map(p => p.complete >= bar), first = done.indexOf(true), last = done.lastIndexOf(true);
  const periods = first < 0 ? [] : tr.periods.slice(first, last + 1).map((p, k) => ({i: first + k, label: p.period, whole: done[first + k], loose: p.unassigned_share}));
  const ratio = metric === 'est_high_share' || metric === 'est_alone_high_share', cash = metric === 'allowed_per_patient';
  const val = (p, i) => { const s = p.series[i]; if(!s) return null; if(metric === 'per_day' && s.clinic_days < 8) return null;
    const x = s[metric]; return x == null ? null : (ratio ? 100 * x : (cash ? x / 100 : x)); };
  const show = all.filter(p => periods.some(q => val(p, q.i) != null));
  const table = show.map(p => periods.map(q => val(p, q.i)));
  const mid = xs => { if(!xs.length) return null; xs.sort((a, b) => a - b); const m = (xs.length - 1) / 2; return (xs[Math.floor(m)] + xs[Math.ceil(m)]) / 2; };
  const others = table.map((_, me) => periods.map((q, k) => mid(table.filter((row, i) => i !== me).map(row => row[k]).filter(x => x != null))));
  const median = periods.map((q, k) => mid(table.map(row => row[k]).filter(x => x != null)));
  const peak = Math.max(1, ...table.flat().filter(x => x != null));
  const pow = Math.pow(10, Math.floor(Math.log10(peak / 4))), frac = peak / 4 / pow;
  const step = (frac <= 1 ? 1 : (frac <= 2 ? 2 : (frac <= 5 ? 5 : 10))) * pow;      // a round step that gives about four gridlines
  const top = Math.ceil(peak / step) * step;
  const fmt = x => x == null ? '–' : (ratio ? Math.round(x) + '%' : (cash ? '$' + Math.round(x).toLocaleString('en-US') : (metric === 'per_day' || metric === 'biopsied_per_100_patients' ? x.toFixed(1) : Math.round(x).toLocaleString('en-US'))));
  const tick = x => ratio ? Math.round(x) + '%' : (cash ? '$' + Math.round(x).toLocaleString('en-US') : Math.round(x).toLocaleString('en-US'));
  const box = v.querySelector('#pvSmall');
  box.innerHTML = show.map((p, k) => { const at = table[k].map(x => x != null).lastIndexOf(true);
    return `<div class="smc"><div class="small"><b>${esc(p.name)}</b> <span class="muted">${esc(tierLabel(p.tier))} · ${fmt(table[k][at])} in ${esc(quarterName(periods[at].label))}</span></div><div class="chartbox" style="height:132px"><canvas id="pvS${k}"></canvas></div></div>`; }).join('')
    || '<div class="muted small">No complete quarters to show.</div>';
  const left = all.filter(p => !show.includes(p));
  v.querySelector('#pvSmallLeft').innerHTML = left.length ? `No quarter with enough to chart for this measure: ${left.map(p => esc(p.name)).join(', ')}.` : '';
  v.querySelector('#pvSmallNote').innerHTML = [PV_METRICS[metric][1] ? esc(PV_METRICS[metric][1].charAt(0).toUpperCase() + PV_METRICS[metric][1].slice(1)) + '.' : '',
    periods.some(q => !q.whole) ? 'A hollow point is a quarter in which a payer\'s remittances are missing for some weeks, so its counts run a little low.' : ''].filter(Boolean).join(' ');
  show.forEach((p, k) => chart('pvS' + k, {type: 'line',
    data: {labels: periods.map(q => quarterName(q.label)), datasets: [
      {label: 'others, median', data: others[k], borderColor: theme.context, backgroundColor: theme.context, borderWidth: 1.5, pointRadius: 0, tension: .25, spanGaps: true},
      {label: p.name, data: table[k], borderColor: theme.series[0], borderWidth: 2, pointRadius: 3, pointHoverRadius: 5,
       pointBackgroundColor: periods.map(q => q.whole ? theme.series[0] : theme.surface), pointBorderColor: periods.map(q => q.whole ? theme.surface : theme.series[0]),
       pointBorderWidth: 1.5, tension: .25, spanGaps: false}]},
    options: {animation: false, interaction: {mode: 'index', intersect: false},
      plugins: {legend: {display: false}, tooltip: {callbacks: {label: c => `${fmt(c.parsed.y)}  ${c.dataset.label}`}}},
      scales: pvAxes(theme, {x: {ticks: {color: theme.muted, font: {size: 10}, maxRotation: 0, autoSkip: true, maxTicksLimit: 4}},
                             y: {max: top, ticks: {color: theme.muted, font: {size: 10}, stepSize: step, callback: x => tick(x)}}})}}));
  v.querySelector('#pvTrendTable').innerHTML = `<div class="scroll"><table class="heat"><thead><tr><th>${esc(PV_METRICS[metric][0])}</th>${periods.map(q => `<th class="num">${esc(q.label)}${q.whole ? '' : ' *'}</th>`).join('')}</tr></thead>
    <tbody>${show.map((p, k) => `<tr><td style="text-align:left">${esc(p.name)}</td>${table[k].map(x => `<td>${fmt(x)}</td>`).join('')}</tr>`).join('')}
    <tr><td style="text-align:left" class="muted">median of those shown</td>${median.map(x => `<td class="muted">${fmt(x)}</td>`).join('')}</tr>
    <tr><td style="text-align:left" class="muted">patients with no clinician named, share of all</td>${periods.map(q => `<td class="muted">${q.loose == null ? '–' : (100 * q.loose).toFixed(1) + '%'}</td>`).join('')}</tr></tbody></table></div>
    ${periods.some(q => !q.whole) ? '<div class="muted">* a payer\'s remittances are missing for some weeks of this quarter</div>' : ''}
    <div class="muted">When the share with no clinician named falls, each clinician's counts rise by about that much without any more patients being seen.</div>`;
}

function pvMix(v, rows, theme){
  const named = PV_GROUPS.flatMap(([, fams]) => fams || []);
  const cents = rows.map(r => PV_GROUPS.map(([, fams]) => (r.families || []).filter(f => fams ? fams.includes(f.family) : !named.includes(f.family)).reduce((a, f) => a + (f.allowed_cents || 0), 0)));
  const totals = cents.map(c => c.reduce((a, x) => a + x, 0));
  chart('pvMix', {type: 'bar',
    data: {labels: rows.map(r => r.name), datasets: PV_GROUPS.map(([label], g) => ({label, data: cents.map((c, i) => totals[i] ? 100 * c[g] / totals[i] : 0), backgroundColor: theme.series[g],
      borderColor: theme.surface, borderWidth: {left: 0, right: 2, top: 0, bottom: 0}, maxBarThickness: 22}))},
    options: {indexAxis: 'y', animation: false,
      plugins: {legend: {position: 'bottom', labels: {color: theme.ink, boxWidth: 12, boxHeight: 12, font: {size: 12}}},
        tooltip: {callbacks: {label: c => `${Math.round(c.parsed.x)}%  ${c.dataset.label}  (${money(cents[c.dataIndex][c.datasetIndex])})`}}},
      scales: {x: {stacked: true, max: 100, grid: {color: theme.line}, border: {color: theme.line}, ticks: {color: theme.muted, callback: x => x + '%'}},
               y: {stacked: true, grid: {display: false}, border: {color: theme.line}, ticks: {color: theme.ink}}}}});
  v.querySelector('#pvMixTable').innerHTML = `<div class="scroll"><table><thead><tr><th>Clinician</th>${PV_GROUPS.map(([l]) => `<th class="num">${esc(l)}</th>`).join('')}<th class="num">All</th></tr></thead>
    <tbody>${rows.map((r, i) => `<tr><td>${esc(r.name)}</td>${cents[i].map(c => `<td class="num">${money(c)}<div class="small muted">${totals[i] ? Math.round(100 * c / totals[i]) + '%' : ''}</div></td>`).join('')}<td class="num"><b>${money(totals[i])}</b></td></tr>`).join('')}</tbody></table></div>`;
}

function pvRecent(el, rc, keep){
  const e = rc.ema || {};
  const list = (rc.providers || []).filter(p => keep({tier: p.tier}));
  if(!(rc.providers || []).length){ el.innerHTML = '<div class="muted small">No Encounter and Charge Lines export has been loaded yet.</div>'; return; }
  const days = (n, more) => n == null ? '–' : `${n0(n)}${more ? '+' : ''} ${Math.round(n) === 1 && !more ? 'day' : 'days'}`;
  el.innerHTML = `<div class="note">These exports include self-pay visits and arrive weeks before the remittances. An export holds only the visits that had become a bill when it was pulled, and a bill appears days after the visit, so a day is counted only when an export covering it was pulled late enough for that clinician's bills to exist. "At face value" is what the same exports say if that is ignored. Where an export does not name the patient, a visit is counted by its bill.</div>
    ${pvTable(list, [
      {h: 'Clinician', f: p => `<b>${esc(p.provider)}</b> <span class="chip">${esc(tierLabel(p.tier))}</span>${p.offices && p.offices.length ? `<span class="sub">${p.offices.map(o => `${esc(o.name)} ${Math.round(100 * o.share)}%`).join(' · ')}</span>` : ''}`},
      {h: 'Days counted', num: 1, f: p => `${n0(p.clinic_days)}${p.counted_from ? `<span class="sub">${dayName(p.counted_from, false)} to ${dayName(p.counted_to, false)}</span>` : ''}`,
       tip: `clinic days (at least ${n0(rc.clinic_day_min)} patients) old enough for that clinician's bills to exist when the export was pulled`},
      {h: 'Patients a day', num: 1, f: p => `<b>${n1(p.patients_per_day)}</b>`},
      {h: 'Insured', num: 1, f: p => pctv(p.insured_share), tip: 'share of those patients with a claim to an insurer'},
      {h: 'Charges a day', num: 1, f: p => money(p.charges_per_day_cents), tip: 'pathology, radiation, skin substitute products and drugs left out'},
      {h: 'New patients a day', num: 1, f: p => n1(p.new_per_day)},
      {h: 'The same days on remittances', num: 1, f: p => p.remit_patients_per_day == null || p.tier === 'staff' ? '–' : `${n1(p.remit_patients_per_day)}${p.remit_share == null ? '' : `<span class="sub">${pctv(p.remit_share)} of the export's count</span>`}`,
       tip: 'patients a day the remittances show for the counted days old enough to have been paid, and what share of the export\'s patients that is. The rest are self-pay, or a payer whose remittances do not arrive here.'},
      {h: 'Visit to bill, median', num: 1, f: p => `<span title="${esc(p.lag.basis ? 'measured on ' + p.lag.basis + ' (' + n0(p.lag.bills) + ')' : '')}">${days(p.lag.median)}</span>`},
      {h: 'Nine in ten within', num: 1, f: p => days(p.lag.p90, p.lag.at_least), tip: 'a plus sign means at least: the exports only show bills written within about three weeks of the visit'},
      {h: 'Days still waiting on bills', num: 1, f: p => p.days_waiting ? `<span class="sev-medium">${n0(p.days_waiting)}</span>` : '0'},
      {h: 'At face value', num: 1, f: p => `<span class="muted">${n1(p.raw_patients_per_day)} a day over ${n0(p.raw_days)} days</span>`},
    ])}
    <div class="small muted" style="margin-top:6px">${rc.counted_from ? `Days counted run from ${dayName(rc.counted_from)} to ${dayName(rc.counted_to)}. ` : ''}The exports cover visits from ${dayName(e.first_service)} to ${dayName(e.last_service)}${e.last_pulled ? `; the newest was pulled ${dayName(e.last_pulled)}` : ''}.
      ${e.exports != null ? `${n0(e.exports_with_bill_dates)} of ${n0(e.exports)} carry the bill creation date, which is what the visit-to-bill columns are measured on.` : ''}
      To keep this current, each week export the full Encounter and Charge Lines layout (the one with Bill Creation Date, Bill ID and Patient ID) by date of service for the last six weeks, not only the week just ended. Overlapping exports are merged, and a later export moves a day's pull date forward.</div>`;
}

async function pvShowDetail(scroll){
  const pv = state.pv, v = pv.el, el = v && v.querySelector('#pvDetail');
  if(!el) return;
  const id = pv.pick, ov = pv.data.ov;
  if(!id){ el.innerHTML = ''; sweepCharts(); return; }
  const key = [id, ov.from, ov.to].join('|');
  let d = pv.detail && pv.detail.key === key ? pv.detail.d : null;
  if(!d){
    el.innerHTML = '<div class="panel muted">loading…</div>';
    try { d = await api('/api/activity/provider/' + encodeURIComponent(id) + '?' + qs({from: ov.from, to: ov.to})); }
    catch(e){
      if(pv.pick !== id || !el.isConnected) return;
      pv.pick = '';
      v.querySelectorAll('tr.row.picked').forEach(t => t.classList.remove('picked'));
      el.innerHTML = `<div class="panel"><span class="sev-medium">That clinician could not be loaded: ${esc(e.message)}</span></div>`;
      return;
    }
    if(pv.pick !== id || !el.isConnected) return;       // another clinician was picked meanwhile, or the page was redrawn
    pv.detail = {key, d};
  }
  if(!d.ready){ el.innerHTML = ''; return; }
  const theme = pvTheme(), who = d.provider || {}, row = ov.providers.find(r => r.provider_id === id) || {};
  const wk = d.weekdays.filter(w => w.clinic_days);
  const maxWk = Math.max(1, ...wk.map(w => w.per_day || 0));
  const whole = d.total_allowed_cents || d.codes.reduce((a, c) => a + (c.allowed_cents || 0), 0) || 1;
  const listed = d.codes.reduce((a, c) => a + (c.allowed_cents || 0), 0);
  const bar = (d.coverage || {}).complete_share || 0.97;
  const low = new Set(d.months.filter(m => m.complete != null && m.complete < bar).map(m => m.month));
  const places = (d.places || []).filter(p => p.clinic_days);
  el.innerHTML = `<div class="panel"><div style="display:flex;justify-content:space-between;align-items:start"><h2>${esc(who.name || id)}: ${monthName(d.from)} to ${monthName(d.to)}</h2><button id="pvClose" title="close">✕</button></div>
    ${row.first_clinic_day ? `<div class="small muted">Clinic days in these months run from ${dayName(row.first_clinic_day)} to ${dayName(row.last_clinic_day)}.</div>` : ''}
    <div class="grid two">
      <div><h3 class="small" style="margin:4px 0">Patients per clinic day, by month</h3><div class="chartbox" style="height:220px"><canvas id="pvMonths"></canvas></div>
        ${low.size ? '<div class="small muted">Months marked * are not complete yet, or a payer\'s remittances are missing for part of them.</div>' : ''}
        <details class="small" style="margin-top:6px"><summary>The same numbers as a table</summary><div class="scroll"><table class="small"><thead><tr><th>Month</th><th class="num">Clinic days</th><th class="num">Patients</th><th class="num">Per clinic day</th><th class="num">New</th><th class="num">Allowed in the exam room</th></tr></thead>
          <tbody>${d.months.map(m => `<tr><td>${monthName(m.month)}${low.has(m.month) ? ' *' : ''}</td><td class="num">${n0(m.clinic_days)}</td><td class="num">${n0(m.patients)}</td><td class="num">${n1(m.per_day)}</td><td class="num">${n0(m.new_visits)}</td><td class="num">${money(m.clinic_allowed_cents)}</td></tr>`).join('')}</tbody></table></div></details></div>
      <div><h3 class="small" style="margin:4px 0">How many clinic days ended at each size</h3><div class="chartbox" style="height:220px"><canvas id="pvHist"></canvas></div>
        <details class="small" style="margin-top:6px"><summary>The same numbers as a table</summary><div class="scroll"><table class="small"><thead><tr><th>Patients that day</th><th class="num">Clinic days</th></tr></thead>
          <tbody>${d.histogram.map(h => `<tr><td>${h.from >= 60 ? '60 or more' : `${n0(h.from)} to ${n0(h.from + 4)}`}</td><td class="num">${n0(h.days)}</td></tr>`).join('')}</tbody></table></div></details></div>
    </div>
    <div class="grid two" style="margin-top:8px">
      <div><h3 class="small" style="margin:4px 0">By weekday</h3><div class="scroll"><table class="small"><thead><tr><th>Day</th><th class="num">Clinic days</th><th class="num">Patients a day</th><th style="width:45%"></th></tr></thead>
        <tbody>${wk.map(w => `<tr><td>${esc(w.day)}</td><td class="num">${n0(w.clinic_days)}</td><td class="num">${n1(w.per_day)}</td><td><div class="bar"><i style="width:${Math.round(100 * (w.per_day || 0) / maxWk)}%;background:var(--pv1)"></i></div></td></tr>`).join('')}</tbody></table></div>
        ${places.length ? `<h3 class="small" style="margin:12px 0 4px">By state of the office</h3><div class="scroll"><table class="small"><thead><tr><th title="the state of the Medicare contractor that paid that day's Medicare claims">State</th><th class="num">Clinic days</th><th class="num">Patients a day, median</th><th class="num">Mean</th></tr></thead>
        <tbody>${places.map(p => `<tr><td>${p.place ? esc(p.place) : '<span class="muted">no Medicare claim that day</span>'}</td><td class="num">${n0(p.clinic_days)}</td><td class="num">${n1(p.per_day)}</td><td class="num">${n1(p.mean)}</td></tr>`).join('')}</tbody></table></div>` : ''}
        <h3 class="small" style="margin:12px 0 4px">By payer</h3><div class="scroll"><table class="small"><thead><tr><th>Payer type</th><th class="num" title="visits, and bills for a slide or a radiation treatment on their own">Bills</th><th class="num">Allowed</th><th class="num">Per bill</th></tr></thead>
        <tbody>${d.payers.map(p => `<tr><td>${esc(PAYER_LABEL[p.payer_kind] || p.payer_kind)}</td><td class="num">${n0(p.encounters)}</td><td class="num">${money(p.allowed_cents)}</td><td class="num">${p.encounters ? money(p.allowed_cents / p.encounters) : '–'}</td></tr>`).join('')}</tbody></table></div></div>
      <div><h3 class="small" style="margin:4px 0">The ${n0(d.codes.length)} largest codes, by allowed dollars</h3><div style="max-height:460px;overflow:auto"><table class="small"><thead><tr><th>Code</th><th class="num">Services</th><th class="num">Units</th><th class="num">Unpaid</th><th class="num">Allowed</th><th class="num" title="share of everything this clinician was allowed in these months">Share of all</th></tr></thead>
        <tbody>${d.codes.map(c => `<tr><td>${esc(c.code)}</td><td class="num">${n0(c.items)}</td><td class="num">${n0(c.units)}</td><td class="num">${c.denied ? n0(c.denied) : ''}</td><td class="num">${money(c.allowed_cents)}</td><td class="num">${(100 * (c.allowed_cents || 0) / whole).toFixed(1)}%</td></tr>`).join('')}
        ${whole - listed > 50 ? `<tr><td class="muted" colspan="4">every other code</td><td class="num muted">${money(whole - listed)}</td><td class="num muted">${(100 * (whole - listed) / whole).toFixed(1)}%</td></tr>` : ''}</tbody></table></div></div>
    </div></div>`;
  el.querySelector('#pvClose').onclick = () => { pv.pick = ''; v.querySelectorAll('tr.row.picked').forEach(t => t.classList.remove('picked')); pvShowDetail(false); };
  chart('pvMonths', {type: 'bar', data: {labels: d.months.map(m => monthName(m.month) + (low.has(m.month) ? ' *' : '')),
    datasets: [{label: 'patients per clinic day (median)', data: d.months.map(m => m.per_day), backgroundColor: theme.series[0], maxBarThickness: 24, borderRadius: {topLeft: 4, topRight: 4}}]},
    options: {animation: false, plugins: {legend: {display: false}, tooltip: {callbacks: {afterLabel: c => { const m = d.months[c.dataIndex]; return `${m.clinic_days} clinic days, ${m.patients} patients, ${m.new_visits} new` + (low.has(m.month) ? ' (month not complete)' : ''); }}}},
      scales: pvAxes(theme)}});
  chart('pvHist', {type: 'bar', data: {labels: d.histogram.map(h => h.from >= 60 ? '60+' : `${h.from} to ${h.from + 4}`),
    datasets: [{label: 'clinic days', data: d.histogram.map(h => h.days), backgroundColor: theme.series[0], maxBarThickness: 24, borderRadius: {topLeft: 4, topRight: 4}}]},
    options: {animation: false, plugins: {legend: {display: false}}, scales: pvAxes(theme, {x: {title: {display: true, text: 'patients seen that day', color: theme.muted}}})}});
  sweepCharts();
  if(scroll) el.scrollIntoView({behavior: 'smooth', block: 'nearest'});
}
