/* RemitWatch dashboard: what each clinician is doing. Loaded after features.js and before the inline script in index.html;
   uses their helpers ($, api, qs, esc, money, chart, state, render, pct).

   Charts follow three rules: one y-axis per chart, a clinician keeps the same treatment whatever the filter, and every
   chart has its numbers in a table next to it. Colours are a fixed order of seven hues checked for colour-blind
   separation on this page's light and dark panels. */

const PV_SERIES = {light: ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#6250d6', '#e34948'],
                   dark: ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#008300', '#9085e9', '#e66767']};
const PV_GROUPS = [['Office visits', ['new_visit', 'est_visit', 'visit_addon']], ['Biopsies', ['biopsy']],
  ['Destruction', ['destruction_premalignant', 'destruction_benign', 'destruction_malignant']],
  ['Excision, repair and Mohs', ['excision', 'repair', 'mohs']], ['Pathology', ['pathology']], ['Radiation', ['radiation']],
  ['Skin substitutes and drugs', ['skin_substitute', 'drug']], ['Other', ['injection', 'phototherapy', 'other']]];
const PV_METRICS = {per_day: ['Patients per clinic day', x => n1(x)], clinic_days: ['Clinic days', x => n0(x)], patients: ['Patients seen', x => n0(x)],
  new_visits: ['New patient visits', x => n0(x)], est_high_share: ['Established visits billed 99214 or 99215', x => pctv(x)],
  biopsied_per_100_patients: ['Patients biopsied per 100 seen', x => n1(x)], allowed_per_patient: ['Allowed per patient seen', x => x == null ? '–' : money(x)]};
const PAYER_LABEL = {medicare: 'Medicare', medicare_advantage: 'Medicare Advantage', commercial: 'Commercial', medicaid: 'Medicaid', supplement: 'Supplement',
  tricare: 'Tricare', va: 'VA', other: 'Other', 'secondary only': 'Secondary payer only'};
const n0 = x => x == null ? '–' : Math.round(x).toLocaleString('en-US');
const n1 = x => x == null ? '–' : Number(x).toFixed(1);
const n2 = x => x == null ? '–' : Number(x).toFixed(2);
const pctv = x => x == null ? '–' : Math.round(100 * x) + '%';
const tierLabel = t => t === 'physician' ? 'MD' : (t === 'nonphysician' ? 'PA / NP' : (t === 'staff' ? 'staff' : ''));
const monthName = m => { if(!m) return ''; const d = new Date(m + '-15T00:00:00Z'); return d.toLocaleString('en-US', {month: 'short', year: 'numeric', timeZone: 'UTC'}); };
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
  return `<div style="overflow:auto"><table class="pvt"><thead><tr>${cols.map(c => `<th class="${c.num ? 'num' : ''}"${c.tip ? ` title="${esc(c.tip)}"` : ''}>${c.h}</th>`).join('')}</tr></thead>
    <tbody>${rows.map(r => `<tr${o.pick ? ` class="row${o.picked === r.provider_id ? ' picked' : ''}" data-pick="${esc(r.provider_id)}"` : ''}>${cols.map(c => `<td class="${c.num ? 'num' : ''}">${c.f(r)}</td>`).join('')}</tr>`).join('')
      || `<tr><td colspan="${cols.length}" class="muted">Nothing in this window.</td></tr>`}</tbody></table></div>`;
}
function pvWho(r, where){
  const w = where && where[r.provider_id];
  return `<b>${esc(r.name || r.provider_id)}</b> <span class="chip">${esc(tierLabel(r.tier))}</span>${w ? `<div class="small muted">${esc(w)}</div>` : ''}`;
}
function pvRange(r, max){
  const p = r.per_day || {};
  if(p.median == null || !max) return '';
  const left = 100 * p.p25 / max, width = Math.max(1.5, 100 * (p.p75 - p.p25) / max), mid = 100 * p.median / max;
  return `<div class="rng" title="middle half of clinic days: ${n1(p.p25)} to ${n1(p.p75)}; busiest day ${n0(p.max)}"><i style="left:${left.toFixed(1)}%;width:${width.toFixed(1)}%"></i><b style="left:${mid.toFixed(1)}%"></b></div>`;
}

async function providersTab(v){
  const pv = state.pv = Object.assign({from: '', to: '', tier: 'all', pick: '', metric: 'per_day'}, state.pv || {});
  const [ov, tr, rc] = await Promise.all([api('/api/activity/overview?' + qs({from: pv.from, to: pv.to})), api('/api/activity/trend'), api('/api/activity/recent')]);
  if(!ov.ready){
    v.innerHTML = `<div class="panel"><h2>Providers</h2><div>${esc(ov.message || 'No provider figures yet.')}</div>${ov.error ? `<div class="small sev-high">Last attempt failed: ${esc(ov.error)}</div>` : ''}</div>`;
    return;
  }
  const cov = ov.coverage, lag = cov.lag || {}, last = cov.last_complete_month;
  const keep = r => pv.tier === 'all' || (pv.tier === 'physician' ? r.tier === 'physician' : r.tier !== 'physician');
  const rows = ov.providers.filter(r => r.clinic_days >= 3 && keep(r));
  const minor = ov.providers.filter(r => r.clinic_days < 3 && keep(r) && (r.patients || r.radiation || r.path_only));
  const where = {};
  (rc.providers || []).forEach(p => { if(p.provider_id && p.offices && p.offices.length) where[p.provider_id] = p.offices.map(o => `${o.name} ${Math.round(100 * o.share)}%`).join(' · '); });
  const preset = !pv.from && !pv.to ? 'year' : 'custom';
  const late = (ov.incomplete_months || []).map(m => `${monthName(m.month)} about ${Math.round(100 * m.complete)}% in`);
  const maxDay = Math.max(5, ...rows.map(r => (r.per_day || {}).p75 || 0)) * 1.1;

  const volume = pvTable(rows, [
    {h: 'Clinician', f: r => pvWho(r, where)},
    {h: 'Clinic days', num: 1, f: r => n0(r.clinic_days), tip: `days with at least ${ov.clinic_day_min} patients seen`},
    {h: 'Per week', num: 1, f: r => n1(r.days_per_week)},
    {h: 'Patients seen', num: 1, f: r => n0(r.patients)},
    {h: 'Per clinic day', num: 1, f: r => `<b>${n0((r.per_day || {}).median)}</b>`, tip: 'median over clinic days'},
    {h: `<span class="muted">0 to ${n0(maxDay)} a day</span>`, f: r => pvRange(r, maxDay)},
    {h: 'New patients a day', num: 1, f: r => n1(r.new_per_day)},
    {h: 'New, share of visits', num: 1, f: r => pctv(r.new_share)},
    {h: 'Panel', num: 1, f: r => n0(r.panel), tip: 'different patients seen in the twelve months ending with the window'},
    {h: 'Kept from the year before', num: 1, f: r => pctv(r.kept_share), tip: 'of the patients seen in the twelve months before, the share seen again by anyone in the practice'},
    {h: 'New patients back within a year', num: 1, f: r => pctv(r.new_back_365), tip: 'new patients first seen 12 to 36 months ago who returned to anyone within 365 days'},
  ], {pick: true, picked: pv.pick});
  const visits = pvTable(rows, [
    {h: 'Clinician', f: r => pvWho(r)},
    {h: 'Established at 99214–5', num: 1, tip: 'share of established patient visits billed 99214 or 99215', f: r => pctv(r.est_high_share)},
    {h: 'New at 99204–5', num: 1, tip: 'share of new patient visits billed 99204 or 99205', f: r => pctv(r.new_high_share)},
    {h: 'Downcoded per 100 visits', num: 1, tip: 'visits a payer paid at a lower level, per 100 visits billed (count in brackets)', f: r => r.downcoded_per_100_visits == null ? '–' : `${n2(r.downcoded_per_100_visits)} <span class="muted small">(${n0((r.downcoded || {}).n)})</span>`},
    {h: 'Medicare share', num: 1, f: r => pctv(r.medicare_share), tip: 'Medicare and Medicare Advantage'},
    {h: 'Biopsied per 100, Medicare', num: 1, f: r => n1(r.biopsied_per_100_medicare)},
    {h: 'Biopsied per 100, others', num: 1, f: r => n1(r.biopsied_per_100_other)},
    {h: 'Lesions per biopsied patient', num: 1, f: r => n2(r.lesions_per_biopsied_patient)},
    {h: 'Cancer treated per 100 biopsied', num: 1, f: r => n1(r.cancer_per_100_biopsied), tip: 'patients biopsied who had Mohs, a malignant excision or destruction, or radiation here within 120 days'},
  ], {pick: true, picked: pv.pick});
  const dollars = pvTable(rows, [
    {h: 'Clinician', f: r => pvWho(r)},
    {h: 'Allowed per patient seen', num: 1, f: r => `<b>${money(r.allowed_per_patient)}</b>`, tip: 'pathology, radiation, skin substitute products and drugs left out'},
    {h: 'Pathology from their biopsies, per patient', num: 1, tip: 'allowed for slides read on specimens this clinician took, billed under the reading physician', f: r => money(r.path_generated_per_patient)},
    {h: 'Allowed per clinic day', num: 1, f: r => money(r.allowed_per_clinic_day)},
    {h: 'Allowed in the exam room', num: 1, f: r => money(r.clinic_allowed_cents)},
    {h: 'Skin substitutes and drugs', num: 1, f: r => r.product_allowed_cents ? money(r.product_allowed_cents) : '–', tip: 'allowed for the products themselves, which mostly covers what they cost'},
    {h: 'Pathology read', num: 1, f: r => r.path_only ? `${n0(r.path_only)} ${r.path_only === 1 ? 'bill' : 'bills'}` : '–'},
    {h: 'Radiation', num: 1, f: r => r.radiation ? `${n0(r.radiation)} ${r.radiation === 1 ? 'treatment' : 'treatments'}` : '–'},
    {h: 'Allowed, everything', num: 1, f: r => money(r.allowed_cents)},
    {h: 'Allowed as share of charges', num: 1, f: r => pctv(r.allowed_share_of_charge)},
  ], {pick: true, picked: pv.pick});

  v.innerHTML = `<div class="panel" style="margin-top:0"><h2>What each clinician is doing</h2>
    <div class="note">Read from the remittances, so it counts insured visits: a self-pay or cosmetic visit never reaches a payer. A clinic day is a day on which the clinician saw at least ${ov.clinic_day_min} patients. A slide read on its own bill and a radiation treatment are counted apart from patients seen.</div>
    <div class="filters">
      <label class="small">Visits <select id="pvPreset"><option value="year" ${preset === 'year' ? 'selected' : ''}>last 12 complete months</option><option value="quarter">last 3 complete months</option><option value="ytd">this year so far</option><option value="all">everything</option><option value="custom" ${preset === 'custom' ? 'selected' : ''}>chosen months</option></select></label>
      <label class="small">from <input id="pvFrom" type="month" value="${esc(ov.from)}" min="${esc(cov.first_month || '')}" max="${esc(cov.last_month || '')}"></label>
      <label class="small">to <input id="pvTo" type="month" value="${esc(ov.to)}" min="${esc(cov.first_month || '')}" max="${esc(cov.last_month || '')}"></label>
      <label class="small">Show <select id="pvTier"><option value="all" ${pv.tier === 'all' ? 'selected' : ''}>everyone</option><option value="nonphysician" ${pv.tier === 'nonphysician' ? 'selected' : ''}>PAs and NPs</option><option value="physician" ${pv.tier === 'physician' ? 'selected' : ''}>physicians</option></select></label>
      <span class="muted small">${monthName(ov.from)} to ${monthName(ov.to)}</span>
    </div>
    <div class="small muted">Half of all visits are paid within ${n0(lag.p50)} days and nine in ten within ${n0(lag.p90)}, so the newest months are still filling in. ${last ? `Visits are complete through ${monthName(last)}.` : ''}
      ${late.length ? `<span class="sev-medium">This window includes months that are not complete (${esc(late.join(', '))}); their counts will rise.</span>` : ''}
      ${ov.unassigned_share ? `${pctv(ov.unassigned_share)} of patients seen could not be tied to a clinician, because the payer named only the group.` : ''}</div></div>
    <div class="panel"><h2>How busy</h2><div class="note">Per clinic day is the median, and the bar is the middle half of that clinician's clinic days on one scale. Panel is the number of different patients seen in the twelve months to ${monthName((rows.find(r => r.panel_month) || {}).panel_month || ov.to)}. Click a row for that clinician's months, weekdays and codes.</div>${volume}
      ${minor.length ? `<div class="small muted" style="margin-top:6px">Fewer than three clinic days in this window: ${minor.map(r => `${esc(r.name)} (${[r.patients ? n0(r.patients) + ' patients' : '', r.radiation ? n0(r.radiation) + ' radiation treatments' : '', r.path_only ? n0(r.path_only) + ' pathology bills' : ''].filter(Boolean).join(', ')})`).join('; ')}.</div>` : ''}</div>
    <div id="pvDetail"></div>
    <div class="panel"><h2>Over time</h2>
      <div class="filters"><label class="small">Show <select id="pvMetric">${Object.keys(PV_METRICS).map(k => `<option value="${k}" ${pv.metric === k ? 'selected' : ''}>${PV_METRICS[k][0]}</option>`).join('')}</select></label>
        <span class="small muted"><span class="key" style="background:var(--pv1)"></span> the clinician &nbsp; <span class="key" style="background:var(--pvc)"></span> median of the others shown</span></div>
      <div class="note">One small chart per clinician, by quarter of service, all on the same scale. Quarters that are not complete yet are left off.</div>
      <div class="sm" id="pvSmall"></div>
      <details class="small" style="margin-top:8px"><summary>The same numbers as a table</summary><div id="pvTrendTable"></div></details></div>
    <div class="panel"><h2>What a visit holds</h2><div class="note">Biopsy rates are shown within Medicare and within everyone else: age drives biopsies, and the payer is the nearest thing to age on a remittance. Cancer treated counts the patients a clinician biopsied who had a skin cancer treated in this practice within 120 days. It is a floor, because treatment somewhere else never shows here.</div>${visits}</div>
    <div class="panel"><h2>What it brings in</h2><div class="note">Allowed is what the payer and the patient together owe under the contract. Per patient seen and "in the exam room" leave out pathology, radiation, and skin substitute products and drugs, which are paid mostly to cover their cost. The pathology column is what the slides from that clinician's biopsies were allowed, billed under the physician who read them.</div>${dollars}
      <h3 class="small" style="margin:14px 0 4px">Where the allowed dollars come from</h3><div class="chartbox" style="height:${Math.max(160, 34 * rows.length + 70)}px"><canvas id="pvMix"></canvas></div>
      <details class="small" style="margin-top:8px"><summary>The same numbers as a table</summary><div id="pvMixTable"></div></details></div>
    <div class="panel"><h2>The newest weeks, from the EMA charge exports</h2><div id="pvRecent"></div></div>
    <div class="panel"><h2>What this cannot show</h2><ul class="small" style="margin:4px 0 0 18px;padding:0">
      <li>Whether a light day was an empty schedule or patients who did not come. That needs the appointment export from EMA (scheduled, arrived, no-show, cancelled, with clinician and office). Put it in the same Dropbox folder and this page can show how full each schedule was.</li>
      <li>What each clinician costs: support staff, rooms, pay.</li>
      <li>Self-pay and cosmetic work before the EMA exports begin.</li></ul></div>`;

  const set = (k, val) => { state.pv[k] = val; render(); };
  v.querySelector('#pvPreset').onchange = e => {
    const p = e.target.value;
    if(p === 'year'){ state.pv.from = ''; state.pv.to = ''; }
    else if(p === 'quarter' && last){ state.pv.from = addMonths(last, -2); state.pv.to = last; }
    else if(p === 'ytd' && last){ state.pv.from = last.slice(0, 4) + '-01'; state.pv.to = last; }
    else if(p === 'all'){ state.pv.from = cov.first_month || ''; state.pv.to = last || ''; }
    else return;
    render();
  };
  v.querySelector('#pvFrom').onchange = e => { state.pv.to = state.pv.to || ov.to; set('from', e.target.value); };
  v.querySelector('#pvTo').onchange = e => { state.pv.from = state.pv.from || ov.from; set('to', e.target.value); };
  v.querySelector('#pvTier').onchange = e => set('tier', e.target.value);
  v.querySelector('#pvMetric').onchange = e => set('metric', e.target.value);
  v.querySelectorAll('tr.row[data-pick]').forEach(trEl => trEl.onclick = () => set('pick', state.pv.pick === trEl.dataset.pick ? '' : trEl.dataset.pick));

  const theme = pvTheme();
  v.style.setProperty('--pv1', theme.series[0]);
  v.style.setProperty('--pvc', theme.context);
  pvSmallMultiples(v, tr, rows, pv.metric, theme);
  pvMix(v, rows, theme);
  pvRecent(v.querySelector('#pvRecent'), rc, keep);
  if(pv.pick) await pvDetail(v.querySelector('#pvDetail'), pv.pick, ov, theme);
}

function pvSmallMultiples(v, tr, rows, metric, theme){
  const order = rows.map(r => r.provider_id);
  const all = tr.providers.filter(p => order.includes(p.provider_id)).sort((a, b) => order.indexOf(a.provider_id) - order.indexOf(b.provider_id));
  const periods = tr.periods.map((p, i) => ({i, label: p.period, complete: p.complete >= 0.97})).filter(p => p.complete);
  const ratio = metric === 'est_high_share';
  const val = (p, i) => { const s = p.series[i]; if(!s) return null; if(metric === 'per_day' && s.clinic_days < 8) return null;
    const x = s[metric]; return x == null ? null : (ratio ? 100 * x : (metric === 'allowed_per_patient' ? x / 100 : x)); };
  const show = all.filter(p => periods.some(q => val(p, q.i) != null));
  const table = show.map(p => periods.map(q => val(p, q.i)));
  const mid = xs => { if(!xs.length) return null; xs.sort((a, b) => a - b); const m = (xs.length - 1) / 2; return (xs[Math.floor(m)] + xs[Math.ceil(m)]) / 2; };
  const others = table.map((_, me) => periods.map((q, k) => mid(table.filter((row, i) => i !== me).map(row => row[k]).filter(x => x != null))));
  const median = periods.map((q, k) => mid(table.map(row => row[k]).filter(x => x != null)));
  const peak = Math.max(1, ...table.flat().filter(x => x != null));
  const pow = Math.pow(10, Math.floor(Math.log10(peak / 4))), frac = peak / 4 / pow;
  const step = (frac <= 1 ? 1 : (frac <= 2 ? 2 : (frac <= 5 ? 5 : 10))) * pow;      // a round step that gives about four gridlines
  const top = Math.ceil(peak / step) * step;
  const fmt = x => x == null ? '–' : (ratio ? Math.round(x) + '%' : (metric === 'allowed_per_patient' ? '$' + Math.round(x).toLocaleString('en-US') : (metric === 'per_day' || metric === 'biopsied_per_100_patients' ? x.toFixed(1) : Math.round(x).toLocaleString('en-US'))));
  const tick = x => ratio ? Math.round(x) + '%' : (metric === 'allowed_per_patient' ? '$' + Math.round(x).toLocaleString('en-US') : Math.round(x).toLocaleString('en-US'));
  const box = v.querySelector('#pvSmall');
  box.innerHTML = show.map((p, k) => { const lastVal = [...table[k]].reverse().find(x => x != null);
    return `<div class="smc"><div class="small"><b>${esc(p.name)}</b> <span class="muted">${esc(tierLabel(p.tier))} · latest ${fmt(lastVal)}</span></div><div class="chartbox" style="height:132px"><canvas id="pvS${k}"></canvas></div></div>`; }).join('')
    || '<div class="muted small">No complete quarters to show.</div>';
  const left = all.filter(p => !show.includes(p));
  if(left.length) box.insertAdjacentHTML('afterend', `<div class="small muted" style="margin-top:6px">Not enough complete quarters yet: ${left.map(p => esc(p.name)).join(', ')}.</div>`);
  show.forEach((p, k) => chart('pvS' + k, {type: 'line',
    data: {labels: periods.map(q => q.label.replace('-', ' ')), datasets: [
      {label: 'others, median', data: others[k], borderColor: theme.context, backgroundColor: theme.context, borderWidth: 1.5, pointRadius: 0, tension: .25, spanGaps: true},
      {label: p.name, data: table[k], borderColor: theme.series[0], backgroundColor: theme.series[0], borderWidth: 2, pointRadius: 3, pointHoverRadius: 5,
       pointBorderColor: theme.surface, pointBorderWidth: 1.5, tension: .25, spanGaps: false}]},
    options: {animation: false, interaction: {mode: 'index', intersect: false},
      plugins: {legend: {display: false}, tooltip: {callbacks: {label: c => `${fmt(c.parsed.y)}  ${c.dataset.label}`}}},
      scales: pvAxes(theme, {x: {ticks: {color: theme.muted, font: {size: 10}, maxRotation: 0, autoSkip: true, maxTicksLimit: 4}},
                             y: {max: top, ticks: {color: theme.muted, font: {size: 10}, stepSize: step, callback: x => tick(x)}}})}}));
  v.querySelector('#pvTrendTable').innerHTML = `<div style="overflow:auto"><table class="heat"><thead><tr><th>${esc(PV_METRICS[metric][0])}</th>${periods.map(q => `<th class="num">${esc(q.label)}</th>`).join('')}</tr></thead>
    <tbody>${show.map((p, k) => `<tr><td style="text-align:left">${esc(p.name)}</td>${table[k].map(x => `<td>${fmt(x)}</td>`).join('')}</tr>`).join('')}
    <tr><td style="text-align:left" class="muted">median of those shown</td>${median.map(x => `<td class="muted">${fmt(x)}</td>`).join('')}</tr></tbody></table></div>`;
}

function pvMix(v, rows, theme){
  const cents = rows.map(r => PV_GROUPS.map(([, fams]) => (r.families || []).filter(f => fams.includes(f.family)).reduce((a, f) => a + (f.allowed_cents || 0), 0)));
  const totals = cents.map(c => c.reduce((a, x) => a + x, 0));
  chart('pvMix', {type: 'bar',
    data: {labels: rows.map(r => r.name), datasets: PV_GROUPS.map(([label], g) => ({label, data: cents.map((c, i) => totals[i] ? 100 * c[g] / totals[i] : 0), backgroundColor: theme.series[g],
      borderColor: theme.surface, borderWidth: {left: 0, right: 2, top: 0, bottom: 0}, maxBarThickness: 22}))},
    options: {indexAxis: 'y', animation: false,
      plugins: {legend: {position: 'bottom', labels: {color: theme.ink, boxWidth: 12, boxHeight: 12, font: {size: 12}}},
        tooltip: {callbacks: {label: c => `${Math.round(c.parsed.x)}%  ${c.dataset.label}  (${money(cents[c.dataIndex][c.datasetIndex])})`}}},
      scales: {x: {stacked: true, max: 100, grid: {color: theme.line}, border: {color: theme.line}, ticks: {color: theme.muted, callback: x => x + '%'}},
               y: {stacked: true, grid: {display: false}, border: {color: theme.line}, ticks: {color: theme.ink}}}}});
  v.querySelector('#pvMixTable').innerHTML = `<div style="overflow:auto"><table><thead><tr><th>Clinician</th>${PV_GROUPS.map(([l]) => `<th class="num">${esc(l)}</th>`).join('')}<th class="num">All</th></tr></thead>
    <tbody>${rows.map((r, i) => `<tr><td>${esc(r.name)}</td>${cents[i].map(c => `<td class="num">${money(c)}<div class="small muted">${totals[i] ? Math.round(100 * c / totals[i]) + '%' : ''}</div></td>`).join('')}<td class="num"><b>${money(totals[i])}</b></td></tr>`).join('')}</tbody></table></div>`;
}

function pvRecent(el, rc, keep){
  const e = rc.ema || {};
  const list = (rc.providers || []).filter(p => p.tier !== 'staff' ? keep({tier: p.tier}) : true);
  if(!list.length){ el.innerHTML = '<div class="muted small">No Encounter and Charge Lines export has been loaded yet.</div>'; return; }
  el.innerHTML = `<div class="note">These exports include self-pay visits and arrive weeks before the remittances. An export holds only the visits that had become a bill when it was pulled, and a bill appears days after the visit, so a day is counted only when an export covering it was pulled late enough for that clinician's bills to exist. "At face value" is what the same exports say if that is ignored.</div>
    ${pvTable(list, [
      {h: 'Clinician', f: p => `<b>${esc(p.provider)}</b> <span class="chip">${esc(p.role || tierLabel(p.tier) || '')}</span>${p.offices && p.offices.length ? `<div class="small muted">${p.offices.map(o => `${esc(o.name)} ${Math.round(100 * o.share)}%`).join(' · ')}</div>` : ''}`},
      {h: 'Days counted', num: 1, f: p => n0(p.clinic_days)},
      {h: 'Patients a day', num: 1, f: p => `<b>${n1(p.patients_per_day)}</b>`},
      {h: 'Insured', num: 1, f: p => pctv(p.insured_share)},
      {h: 'Charges a day', num: 1, f: p => money(p.charges_per_day_cents), tip: 'pathology and radiation left out'},
      {h: 'New patients a day', num: 1, f: p => n1(p.new_per_day)},
      {h: 'Same days on remittances', num: 1, f: p => n1(p.remit_patients_per_day), tip: 'insured patients a day paid so far for the days counted'},
      {h: 'Visit to bill, median', num: 1, f: p => p.lag.median == null ? '–' : `${n0(p.lag.median)} days`},
      {h: 'Nine in ten within', num: 1, f: p => p.lag.p90 == null ? '–' : `${n0(p.lag.p90)} days`},
      {h: 'Days still waiting on bills', num: 1, f: p => p.days_waiting ? `<span class="sev-medium">${n0(p.days_waiting)}</span>` : '0'},
      {h: 'At face value', num: 1, f: p => `<span class="muted">${n1(p.raw_patients_per_day)} a day over ${n0(p.raw_days)} days</span>`},
    ])}
    <div class="small muted" style="margin-top:6px">Exports cover visits from ${esc(e.first_service || '?')} to ${esc(e.last_service || '?')}; the newest bill in them was created ${esc(e.last_bill_created || '?')}. ${e.exports_with_bill_dates != null ? `${n0(e.exports_with_bill_dates)} of ${n0(e.exports)} exports carry the bill creation date.` : ''}
      To keep this current, each week export the full Encounter and Charge Lines layout (the one with Bill Creation Date, Bill ID and Patient ID) for the last six weeks, not only the week just ended. Overlapping exports are merged.</div>`;
}

async function pvDetail(el, id, ov, theme){
  const d = await api('/api/activity/provider/' + encodeURIComponent(id) + '?' + qs({from: ov.from, to: ov.to}));
  if(!d.ready){ el.innerHTML = ''; return; }
  const who = d.provider || {};
  const wk = d.weekdays.filter(w => w.clinic_days);
  const maxWk = Math.max(1, ...wk.map(w => w.per_day || 0));
  const allAllowed = d.codes.reduce((a, c) => a + (c.allowed_cents || 0), 0) || 1;
  el.innerHTML = `<div class="panel"><div style="display:flex;justify-content:space-between;align-items:start"><h2>${esc(who.name || id)}: ${monthName(d.from)} to ${monthName(d.to)}</h2><button id="pvClose">✕</button></div>
    <div class="grid two">
      <div><h3 class="small" style="margin:4px 0">Patients per clinic day, by month</h3><div class="chartbox" style="height:220px"><canvas id="pvMonths"></canvas></div></div>
      <div><h3 class="small" style="margin:4px 0">How many clinic days ended at each size</h3><div class="chartbox" style="height:220px"><canvas id="pvHist"></canvas></div></div>
    </div>
    <div class="grid two" style="margin-top:8px">
      <div><h3 class="small" style="margin:4px 0">By weekday</h3><table class="small"><thead><tr><th>Day</th><th class="num">Clinic days</th><th class="num">Patients a day</th><th style="width:45%"></th></tr></thead>
        <tbody>${wk.map(w => `<tr><td>${w.day}</td><td class="num">${n0(w.clinic_days)}</td><td class="num">${n1(w.per_day)}</td><td><div class="bar"><i style="width:${Math.round(100 * (w.per_day || 0) / maxWk)}%;background:var(--pv1)"></i></div></td></tr>`).join('')}</tbody></table>
        <h3 class="small" style="margin:12px 0 4px">By payer</h3><table class="small"><thead><tr><th>Payer type</th><th class="num">Encounters</th><th class="num">Allowed</th><th class="num">Per encounter</th></tr></thead>
        <tbody>${d.payers.map(p => `<tr><td>${esc(PAYER_LABEL[p.payer_kind] || p.payer_kind)}</td><td class="num">${n0(p.encounters)}</td><td class="num">${money(p.allowed_cents)}</td><td class="num">${p.encounters ? money(p.allowed_cents / p.encounters) : '–'}</td></tr>`).join('')}</tbody></table></div>
      <div><h3 class="small" style="margin:4px 0">Codes billed, by allowed dollars</h3><div style="max-height:420px;overflow:auto"><table class="small"><thead><tr><th>Code</th><th class="num">Lines</th><th class="num">Units</th><th class="num">Unpaid</th><th class="num">Allowed</th><th class="num">Share</th></tr></thead>
        <tbody>${d.codes.map(c => `<tr><td>${esc(c.code)}</td><td class="num">${n0(c.items)}</td><td class="num">${n0(c.units)}</td><td class="num">${c.denied ? n0(c.denied) : ''}</td><td class="num">${money(c.allowed_cents)}</td><td class="num">${Math.round(100 * (c.allowed_cents || 0) / allAllowed)}%</td></tr>`).join('')}</tbody></table></div></div>
    </div></div>`;
  el.querySelector('#pvClose').onclick = () => { state.pv.pick = ''; render(); };
  const low = new Set((d.coverage.months || []).filter(m => m.complete < 0.97).map(m => m.month));
  chart('pvMonths', {type: 'bar', data: {labels: d.months.map(m => monthName(m.month) + (low.has(m.month) ? ' *' : '')),
    datasets: [{label: 'patients per clinic day (median)', data: d.months.map(m => m.per_day), backgroundColor: theme.series[0], maxBarThickness: 24, borderRadius: {topLeft: 4, topRight: 4}}]},
    options: {animation: false, plugins: {legend: {display: false}, tooltip: {callbacks: {afterLabel: c => { const m = d.months[c.dataIndex]; return `${m.clinic_days} clinic days, ${m.patients} patients, ${m.new_visits} new` + (low.has(m.month) ? ' (month not complete yet)' : ''); }}}},
      scales: pvAxes(theme)}});
  chart('pvHist', {type: 'bar', data: {labels: d.histogram.map(h => h.from >= 60 ? '60+' : `${h.from} to ${h.from + 4}`),
    datasets: [{label: 'clinic days', data: d.histogram.map(h => h.days), backgroundColor: theme.series[0], maxBarThickness: 24, borderRadius: {topLeft: 4, topRight: 4}}]},
    options: {animation: false, plugins: {legend: {display: false}}, scales: pvAxes(theme, {x: {title: {display: true, text: 'patients seen that day', color: theme.muted}}})}});
  el.scrollIntoView({behavior: 'smooth', block: 'nearest'});
}
