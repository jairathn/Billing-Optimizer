/* ==================================================================== ASC final rule
   A view of the Calculator tab, for the owner. The practice as a whole: the clinician picked above does not change it.

   1. What the rule year does to the ASC's cases: every case the practice's surgeons billed at its ASC over the window
      (/api/activity/asc_rule), a year at that pace, priced at Medicare's ASC rates for the year before and the rule
      year (AscModel, asc_model.js, which prices each case with the strategy lab's own arithmetic). Each kind of payer
      pays the ASC a share of Medicare's rate, which starts at what that kind pays the practice's surgeons against
      Medicare and can be changed here; the practice's share of the ASC turns the ASC's figures into the practice's.
   2. ASCA's rate calculator: Medicare's ASC amounts for any code in any county, national and local, before and after
      sequestration, with the copayment (/api/activity/asc_rates: CMS's own rates, worked out as ASCA's workbook does).

   Settings here last for the visit; the lab's ASC card is where they are saved with a scenario, and its figures (the
   practice's share, the plans' share of Medicare's ASC rates, quality reporting) are where these start. */

const ASC_LOOK_PAGE = 100;
// A search for the rate lookup: the route reads at most 200 codes or ranges (ascrates._match), 2,400 characters with
// the spaces between them. The whole search is sent first; a server that still takes only 400 characters answers 422,
// and is then sent the whole codes that fit in 400.
const ASC_Q_CODES = 200, ASC_Q_WIDE = 2400, ASC_Q_MAX = 400;

function ascStyle(){
  if(document.getElementById('ascStyle')) return;
  const st = document.createElement('style'); st.id = 'ascStyle';
  st.textContent = `.asc .sub{display:block;color:var(--muted);font-size:11px;font-weight:400;white-space:normal}
.asc table.mini th{white-space:nowrap}
@media(max-width:640px){.asc table.calc-cards td:empty{display:none}}`;
  document.head.appendChild(st);
}

function ascLabParams(cs){ const c = ((cs.lab || {}).chosen || {}).asc; return (c && c.params) || {}; }
const ascSigned = c => { const v = Math.round(c || 0); return v > 0 ? '+' + money(v) : v < 0 ? '−' + money(-v) : '$0'; };
const ascSigned2 = c => { const v = Math.round(c || 0); return v > 0 ? '+' + money2(v) : v < 0 ? '−' + money2(-v) : '$0.00'; };
const ascPct = (a, b) => (a > 0 && b != null) ? ((b / a - 1) >= 0 ? '+' : '−') + Math.abs((b / a - 1) * 100).toFixed(1) + '%' : '–';
const ascN = n => n >= 10 ? Math.round(n).toLocaleString() : (Math.round(n * 10) / 10).toLocaleString();

async function ascDraw(v){
  const cs = state.calc, A = cs.asc = cs.asc || {};
  let body = v.querySelector('#calcBody');
  if(!body) return;
  const key = String(cs.months);
  if(!A.data || A.data._for !== key){
    body.innerHTML = `<div class="panel"><h2>ASC final rule</h2><div class="note">Reading the ASC's cases…</div></div>`;
    const d = await api('/api/activity/asc_rule?' + qs({months: cs.months}));
    body = v.querySelector('#calcBody');
    if(state.tab !== 'calculator' || cs.view !== 'asc' || !body) return;
    if(!d.ready){ body.innerHTML = `<div class="panel"><h2>ASC final rule</h2><div class="note">${esc(d.message || 'The figures are not built yet.')}</div></div>`; return; }
    d._for = key;
    const fresh = AscModel.defaults(d, ascLabParams(cs));
    // what was changed on this visit stays; what was measured follows the new window
    if(A.s){
      for(const k of AscModel.KINDS){ const o = A.s.kinds[k]; if(!o) continue; fresh.kinds[k].follows = o.follows; if(o.from === 'entered') fresh.kinds[k] = o; }
      fresh.own = A.s.own; fresh.ascqr = A.s.ascqr;
    }
    A.data = d; A.s = fresh;
  }
  const d = A.data;
  // the surgeons' fees at Medicare Advantage move with Medicare's schedule for the plans the Calculator has moving with it
  // (its tick on each plan under Contracts), read at every visit to this view: all of them unless one is unticked there
  const ma = AscModel.maFollowing((cs.data || {}).payers, (cs.x || {}).indexed);
  A.s.kinds.medicare_advantage.pro_follows = ma == null ? 1 : ma;
  ascStyle();
  // a copy of the figures built before the provider tables kept the ASC's cases has none to show: say so, with no
  // tiles of $0 that would read as no case at the ASC
  const counted = AscModel.counted(d);
  body.innerHTML = `<div class="asc">
    <div class="panel" id="ascRule">${counted ? ascRuleHtml(d, A.s) : ascNotCountedHtml(d)}</div>
    ${counted ? `<div class="grid two"><div><div class="panel" id="ascTypes"></div></div><div><div class="panel" id="ascCodes"></div></div></div>` : ''}
    <div class="panel" id="ascLook"></div></div>`;
  if(counted){ ascRuleWire(v); ascRuleUpdate(v); }
  await ascLookDraw(v);
}

const ASC_NOT_COUNTED = "The ASC's cases are not in these figures yet. They are counted the next time the provider tables are rebuilt: open the Data tab and press Sync and analyse now. Until then nothing is priced here, so that an empty count is not read as no case at the ASC.";

function ascNotCountedHtml(d){
  return `<h2>What ${esc(d.rule_year)} does to the ASC's cases</h2>
    <div class="note">${esc(ASC_NOT_COUNTED)} The rate calculator below answers for any code.</div>`;
}

// the cases panel: the settings (drawn once) and the cells ascRuleUpdate fills
function ascRuleHtml(d, s){
  const py = d.prior_year, ry = d.rule_year, site = d.site || {}, lab = d.labels || {};
  const R = AscModel.evaluate(d, s);
  const wi = site.wage_index || {};
  const kinds = AscModel.KINDS.filter(k => R.kinds[k].n > 0 || k === 'medicare');
  const shareCell = k => {
    if(k === 'medicare') return `<td class="num" data-label="Pays the ASC">100%<span class="sub">CMS's rates</span></td>`;
    const o = s.kinds[k], m = (d.kinds || {})[k];
    const why = o.from === 'lab' ? "from the lab's ASC card" : m ? `what they pay the surgeons: ${Math.round(m.ratio * 100)}% of Medicare` : "not measured: Medicare's 100% until entered";
    return `<td class="num" data-label="Pays the ASC"><input type="number" id="ascShare-${k}" class="calc-num" min="0" max="500" step="any" value="${esc(Math.round(o.share * 1000) / 10)}" aria-label="${esc(AscModel.LABEL[k])}: share of Medicare's ASC rate, %"> %<span class="sub" id="ascShareWhy-${k}">${esc(why)}</span></td>`;
  };
  const followCell = k => k === 'medicare' ? `<td data-label="Follows Medicare's yearly rates">always</td>`
    : `<td data-label="Follows Medicare's yearly rates"><label class="small"><input type="checkbox" id="ascFollow-${k}" ${s.kinds[k].follows ? 'checked' : ''}> yes</label></td>`;
  const own = s.own == null ? '' : Math.round(s.own * 1000) / 10;
  const from = d.cases && d.cases.from === 'lines' ? 'Read from the remittance lines.' : 'Read from the provider tables the daily run builds.';
  return `
    <h2>What ${esc(ry)} does to the ASC's cases</h2>
    <div class="small muted">${esc(site.label || 'The')} ASC: ${esc(ascTitle(site.county))} County, ${esc(site.state || '')} (CBSA ${esc(site.cbsa || '?')} ${esc(site.cbsa_name || '')}), ASC wage index ${esc(wi[py] != null ? wi[py] : '?')} in ${esc(py)} and ${esc(wi[ry] != null ? wi[ry] : '?')} in ${esc(ry)}.
      The cases the practice's surgeons billed there in ${esc(d.from)} to ${esc(d.to)} (a closure after a Mohs the same day, or an excision with its repair, billed on the ASC's claim), at that pace for a year, priced at Medicare's ASC rates of ${esc(lab[py] || py)} and of ${esc(lab[ry] || ry)}${d.source ? ` (${esc(d.source)})` : ''}. ${esc(from)}</div>
    <div class="grid kpis mt-3" id="ascTiles"></div>
    <table class="calc-cards mt-3" id="ascKinds">
      <thead><tr><th>Payer</th><th class="num">Cases a year</th><th class="num">Pays the ASC, as a share of Medicare's rate</th><th>Follows Medicare's yearly rates</th>
        <th class="num">${esc(py)}</th><th class="num">${esc(ry)}</th><th class="num">Change</th></tr></thead>
      <tbody>${kinds.map(k => `<tr><td>${esc(AscModel.LABEL[k])}</td><td class="num" data-label="Cases a year" id="ascKn-${k}"></td>${shareCell(k)}${followCell(k)}
        <td class="num" data-label="${esc(py)}" id="ascK0-${k}"></td><td class="num" data-label="${esc(ry)}" id="ascK1-${k}"></td><td class="num" data-label="Change" id="ascKd-${k}"></td></tr>`).join('')}
        <tr class="calc-total"><td><b>All payers</b></td><td class="num" data-label="Cases a year" id="ascKn-all"></td><td></td><td></td>
        <td class="num" data-label="${esc(py)}" id="ascK0-all"></td><td class="num" data-label="${esc(ry)}" id="ascK1-all"></td><td class="num" data-label="Change" id="ascKd-all"></td></tr></tbody>
    </table>
    <div class="filters" style="margin-top:10px;gap:14px;flex-wrap:wrap;align-items:center">
      <label class="small">The practice's share of the ASC <input type="number" id="ascOwn" class="calc-num" min="0" max="100" step="any" value="${esc(own)}" placeholder="not entered" style="width:112px" aria-label="The practice's share of the ASC, %"> %</label>
      <label class="small"><input type="checkbox" id="ascQr" ${s.ascqr ? 'checked' : ''}> The ASC does not meet quality reporting (ASCQR): about 2% less on A2, G2 and P2 codes, both years</label>
    </div>
    <div id="ascMedicare" class="small mt-2"></div>
    <h3 style="margin:14px 0 4px">The surgeons' fees for the same cases</h3>
    <table class="calc-cards" id="ascPro"><thead><tr><th>Payer</th><th class="num">${esc(py)}</th><th class="num">${esc(ry)}</th><th class="num">Change</th></tr></thead><tbody></tbody></table>
    <div class="note">Medicare's physician fee schedule for ${esc(py)} and ${esc(ry)}: the Mohs in the clinic at the office amount, the closure at the ASC at the facility amount, the 50% rule across the two claims; Medicare Advantage at what it pays the surgeons against Medicare. ${esc(ascMaNote(s, py, ry))} Commercial and other plans pay the surgeons by their own contracts: the Calculator's waterfall has each plan's ${esc(ry)} change, and the ${esc(ry)} same-day visit rule, which is not counted here.</div>
    <div class="note" id="ascNotes"></div>`;
}

function ascTitle(s){ return String(s || '').toLowerCase().replace(/\b[a-z]/g, c => c.toUpperCase()); }

// what the Medicare Advantage surgeon fees follow: the Calculator's tick on each plan ("moves with Medicare")
function ascMaNote(s, py, ry){
  const f = ((s.kinds || {}).medicare_advantage || {}).pro_follows;
  if(f == null || f >= 0.9995) return `A Medicare Advantage plan moves with Medicare's ${ry} schedule unless the Calculator has it unticked under Contracts ("moves with Medicare in ${ry}").`;
  const held = Math.round((1 - f) * 100);
  return `The Calculator has ${held < 1 ? 'under 1' : held}% of its Medicare Advantage money at plans unticked for "moves with Medicare in ${ry}": that share of the Medicare Advantage surgeon fees is held at the ${py} amount.`;
}

function ascRuleWire(v){
  const A = state.calc.asc, s = A.s, box = v.querySelector('#ascRule');
  const soon = () => { cancelAnimationFrame(A.raf); A.raf = requestAnimationFrame(() => ascRuleUpdate(v)); };
  for(const k of AscModel.KINDS){
    const inp = box.querySelector(`#ascShare-${k}`);
    if(inp){
      const set = e => {
        const raw = String(inp.value).trim();
        if(raw === '' && !(inp.validity && inp.validity.badInput)){
          // an emptied box goes back to where it started, and says so once typing ends
          const fresh = AscModel.defaults(A.data, ascLabParams(state.calc)).kinds[k];
          s.kinds[k].share = fresh.share; s.kinds[k].from = fresh.from;
          if(e.type === 'change') inp.value = Math.round(fresh.share * 1000) / 10;
          soon(); return;
        }
        const val = parseFloat(raw);
        if(!isFinite(val)) return;
        s.kinds[k].share = Math.min(5, Math.max(0, val / 100));
        s.kinds[k].from = 'entered';
        const why = box.querySelector(`#ascShareWhy-${k}`); if(why) why.textContent = 'entered here';
        if(e.type === 'change') inp.value = Math.round(s.kinds[k].share * 1000) / 10;
        soon();
      };
      inp.oninput = set; inp.onchange = set;
    }
    const cb = box.querySelector(`#ascFollow-${k}`);
    if(cb) cb.onchange = () => { s.kinds[k].follows = cb.checked; soon(); };
  }
  const own = box.querySelector('#ascOwn');
  const setOwn = e => {
    const raw = String(own.value).trim();
    if(raw === ''){ if(!(own.validity && own.validity.badInput)){ s.own = null; soon(); } return; }
    const val = parseFloat(raw);
    if(!isFinite(val)) return;
    s.own = Math.min(1, Math.max(0, val / 100));
    if(e.type === 'change') own.value = Math.round(s.own * 1000) / 10;
    soon();
  };
  own.oninput = setOwn; own.onchange = setOwn;
  box.querySelector('#ascQr').onchange = e => { s.ascqr = e.target.checked; soon(); };
}

// fills every figure from the settings as they stand; the inputs are left alone
function ascRuleUpdate(v){
  const A = state.calc.asc, d = A.data, s = A.s;
  const box = v.querySelector('#ascRule');
  if(!box || !d) return;
  const R = AscModel.evaluate(d, s), py = d.prior_year, ry = d.rule_year;
  const put = (id, html) => { const el = box.querySelector('#' + id); if(el) el.innerHTML = html; };
  for(const k of AscModel.KINDS){
    const K = R.kinds[k];
    put(`ascKn-${k}`, ascN(K.n));
    put(`ascK0-${k}`, money(K.fee[py]));
    put(`ascK1-${k}`, money(K.fee[ry]));
    put(`ascKd-${k}`, `${ascSigned(K.fee[ry] - K.fee[py])}<span class="sub">${ascPct(K.fee[py], K.fee[ry])}</span>`);
  }
  put('ascKn-all', ascN(R.n));
  put('ascK0-all', `<b>${money(R.total[py])}</b>`);
  put('ascK1-all', `<b>${money(R.total[ry])}</b>`);
  put('ascKd-all', `<b>${ascSigned(R.total[ry] - R.total[py])}</b><span class="sub">${ascPct(R.total[py], R.total[ry])}</span>`);
  const tile = (label, a, b, sub) => `<div class="kpi" data-go=""><div class="l">${esc(label)}</div><div class="v">${a == null ? '–' : money(b)}</div>
    <div class="s">${a == null ? esc(sub || '') : `${esc(py)}: ${money(a)}. Change ${ascSigned(b - a)} (${ascPct(a, b)})`}</div></div>`;
  const pro = R.pro;
  put('ascTiles', [
    tile(`The ASC's facility fees, a year (${ry})`, R.total[py], R.total[ry]),
    tile(`Medicare's own payment after sequestration (${ry})`, R.medicare.paid[py], R.medicare.paid[ry]),
    R.own ? tile(`The practice's share of the ASC's fees (${ry})`, R.own[py], R.own[ry]) : tile("The practice's share of the ASC's fees", null, null, "Enter the practice's share of the ASC below."),
    tile(`The surgeons' fees, Medicare and Medicare Advantage (${ry})`, pro[py], pro[ry]),
  ].join(''));
  put('ascMedicare', R.kinds.medicare.n > 0
    ? `Of Medicare's ${money(R.kinds.medicare.fee[ry])} in ${esc(ry)}: Medicare pays ${money(R.medicare.paid[ry])} after its 2% sequestration (${money(R.medicare.paid[py])} in ${esc(py)}); the patients' 20% coinsurance is ${money(R.medicare.coinsurance[ry])} (${money(R.medicare.coinsurance[py])}), most of it paid by their supplement plans.`
    : '');
  const tb = box.querySelector('#ascPro tbody');
  if(tb) tb.innerHTML = AscModel.PRO_KINDS.filter(k => R.kinds[k].n > 0).map(k => { const P = R.kinds[k].pro;
    return `<tr><td>${esc(AscModel.LABEL[k])}</td><td class="num" data-label="${esc(py)}">${money(P[py])}</td><td class="num" data-label="${esc(ry)}">${money(P[ry])}</td><td class="num" data-label="Change">${ascSigned(P[ry] - P[py])}<span class="sub">${ascPct(P[py], P[ry])}</span></td></tr>`; }).join('')
    || `<tr><td colspan="4" class="muted">No Medicare or Medicare Advantage case at the ASC in the window.</td></tr>`;
  const notes = [];
  if(!R.n) notes.push(`No case in ${d.from} to ${d.to} was billed at the ASC (place of service 24). The rate calculator below still answers for any code.`);
  if(R.unpriced > 0.05) notes.push(`${ascN(R.unpriced)} cases a year could not be priced: ${R.missing.length ? 'no ASC rate or physician amount for ' + R.missing.join(', ') : 'a code is missing'}. They are left out of every figure above.`);
  if(R.split > 0.05) notes.push(`${ascN(R.split)} cases a year were billed partly in the clinic and partly at the ASC the same day; they are left out.`);
  notes.push("Counted: a Mohs case whose closure was billed on the ASC's claim, and an excision billed there with its repair. Not counted: a closure done on a later day, an excision with no repair, anything else done at the ASC.");
  notes.push("The ASC's own remittances are not in RemitWatch (the 835s are the practice's professional claims), so its fees are priced from Medicare's rates, not read. A plan's share starts at what that kind of payer pays the practice's surgeons for these procedures against what Medicare pays them; enter the ASC contract's own share where it is known. A plan whose contract does not follow Medicare's yearly rates is paid in " + ry + " what it was paid in " + py + ".");
  put('ascNotes', notes.map(esc).join('<br>'));
  ascTypesDraw(v, R);
  ascCodesDraw(v, R);
}

function ascTypesDraw(v, R){
  const A = state.calc.asc, d = A.data, py = d.prior_year, ry = d.rule_year;
  const box = v.querySelector('#ascTypes');
  if(!box) return;
  const rows = AscModel.TYPES.filter(([t]) => (R.types[t] || {}).priced > 0).map(([t, label]) => {
    const T = R.types[t], per = Y => T.fee[Y] / T.priced, pro = Y => T.pro[Y] / T.priced;
    return `<tr><td>${esc(label)}</td><td class="num" data-label="Cases a year">${ascN(T.n)}</td>
      <td class="num" data-label="The ASC, ${esc(py)}">${money2(per(py))}</td><td class="num" data-label="The ASC, ${esc(ry)}">${money2(per(ry))}<span class="sub">${ascPct(per(py), per(ry))}</span></td>
      <td class="num" data-label="The surgeon, ${esc(py)}">${money2(pro(py))}</td><td class="num" data-label="The surgeon, ${esc(ry)}">${money2(pro(ry))}<span class="sub">${ascPct(pro(py), pro(ry))}</span></td></tr>`;
  });
  box.innerHTML = `<h2>A case, at Medicare's rates</h2>
    <div class="small muted">What Medicare pays for one case of each type as the ASC's cases were billed (the ASC's claim after its discounting; the surgeon's fees for the Mohs and the closure after the 50% rule), whoever the payer was.</div>
    <table class="calc-cards mt-2"><thead><tr><th>Case</th><th class="num">Cases a year</th><th class="num">The ASC, ${esc(py)}</th><th class="num">The ASC, ${esc(ry)}</th><th class="num">The surgeon, ${esc(py)}</th><th class="num">The surgeon, ${esc(ry)}</th></tr></thead>
    <tbody>${rows.join('') || '<tr><td colspan="6" class="muted">No case to show.</td></tr>'}</tbody></table>`;
}

function ascCodesDraw(v, R){
  const A = state.calc.asc, d = A.data, py = d.prior_year, ry = d.rule_year, site = d.site || {};
  const box = v.querySelector('#ascCodes');
  if(!box) return;
  const codes = Object.keys(R.codes).sort();
  const pi = d.pi || {};
  const rows = codes.map(c => {
    const C = (d.codes || {})[c] || {}, a = C[py] || {}, b = C[ry] || {};
    const ind = b.pi ? `<span title="${esc(pi[b.pi] || '')}">${esc(b.pi)}</span>${a.pi && a.pi !== b.pi ? `<span class="sub">${esc(py)}: ${esc(a.pi)}</span>` : ''}` : '–';
    return `<tr><td>${esc(c)}</td><td class="num" data-label="Units a year">${ascN(R.codes[c])}</td><td data-label="Indicator">${ind}</td>
      <td data-label="Discounted">${b.mpd ? 'yes' : b.pi ? 'no' : '–'}</td>
      <td class="num" data-label="National, ${esc(ry)}">${money2(b.national)}<span class="sub">${esc(py)}: ${money2(a.national)}</span></td>
      <td class="num" data-label="${esc(site.label || 'ASC')}, ${esc(ry)}">${money2(b.local)}<span class="sub">${esc(py)}: ${money2(a.local)}</span></td>
      <td class="num" data-label="Change">${a.local && b.local != null ? ascSigned2(b.local - a.local) : '–'}<span class="sub">${ascPct(a.local, b.local)}</span></td></tr>`;
  });
  box.innerHTML = `<h2>By code</h2>
    <div class="small muted">Medicare's rate for each code billed on the ASC's claims, before discounting: nationally and at ${esc(site.label || 'the ASC')}, wage-adjusted. Hover an indicator for what it means.</div>
    <table class="calc-cards mt-2"><thead><tr><th>Code</th><th class="num">Units a year</th><th>Indicator</th><th>Discounted</th><th class="num">National</th><th class="num">${esc(site.label || 'ASC')}</th><th class="num">Change</th></tr></thead>
    <tbody>${rows.join('') || '<tr><td colspan="7" class="muted">No code to show.</td></tr>'}</tbody></table>
    ${codes.length ? `<button class="ghost" id="ascLookCodes" style="margin-top:6px">Look these codes up in the rate calculator below</button>` : ''}`;
  const b = box.querySelector('#ascLookCodes');
  if(b) b.onclick = () => { const L = A.look; if(!L) return; L.q = codes.join(' '); const q = v.querySelector('#ascQ'); if(q) q.value = L.q; ascLookRun(v, 0); v.querySelector('#ascLook').scrollIntoView({behavior: 'smooth', block: 'start'}); };
}

// ------------------------------------------------------------------ ASCA's rate calculator
function ascDefaultQuery(A){
  const C = (A.data || {}).codes || {};
  return Object.keys(C).filter(c => !/^1731/.test(c)).sort().join(' ');
}

async function ascLookDraw(v){
  const cs = state.calc, A = cs.asc, box = v.querySelector('#ascLook');
  if(!box) return;
  if(!A.places){
    box.innerHTML = `<h2>ASC rate calculator</h2><div class="note">Reading the counties…</div>`;
    try { A.places = await api('/api/activity/asc_places'); }
    catch(e){ if(box.isConnected) box.innerHTML = `<h2>ASC rate calculator</h2><div class="note">${esc(e.message)}</div>`; return; }
    if(!box.isConnected) return;
  }
  const P = A.places, home = P.home || {};
  const L = A.look = A.look || {state: home.state || '', county: home.county || '', q: ascDefaultQuery(A), national: false};
  const counties = st => (P.counties || {})[st] || [];
  const countyOpts = st => counties(st).map(([c]) => `<option value="${esc(c)}" ${c === L.county ? 'selected' : ''}>${esc(ascTitle(c))}</option>`).join('');
  box.innerHTML = `<h2>ASC rate calculator</h2>
    <div class="small muted">Medicare's payment to an ASC for any code on CMS's ASC addenda, nationally and in a county: the ${esc(P.rule_year)} final rule, against ${esc(P.prior_year)}. The rates are CMS's own. They are worked out as ASCA's rate calculator works them out, except that skin substitute supplies (S2) are not wage-adjusted, as CMS pays them. A code: 14040; the start of one: 1404; a range: 11600-11646; several, with spaces or commas. Empty: every code.</div>
    <div class="filters" style="margin-top:8px;gap:8px;flex-wrap:wrap">
      <select id="ascState" aria-label="State">${(P.states || []).map(([st, n]) => `<option value="${esc(st)}" ${st === L.state ? 'selected' : ''}>${esc(ascTitle(n))}</option>`).join('')}</select>
      <select id="ascCounty" aria-label="County">${countyOpts(L.state)}</select>
      <input id="ascQ" type="search" value="${esc(L.q)}" placeholder="Codes: 14040 1160 11600-11646" aria-label="Codes" style="min-width:min(320px,80vw)">
      <button id="ascGo">Look up</button>
      <label class="small"><input type="checkbox" id="ascNat" ${L.national ? 'checked' : ''}> National amounts too</label>
    </div>
    <div id="ascLookOut" style="margin-top:8px"></div>`;
  const st = box.querySelector('#ascState'), co = box.querySelector('#ascCounty'), q = box.querySelector('#ascQ');
  st.onchange = () => { L.state = st.value; const first = counties(L.state)[0]; L.county = first ? first[0] : ''; co.innerHTML = countyOpts(L.state); ascLookRun(v, 0); };
  co.onchange = () => { L.county = co.value; ascLookRun(v, 0); };
  box.querySelector('#ascGo').onclick = () => { L.q = q.value; ascLookRun(v, 0); };
  q.onkeydown = e => { if(e.key === 'Enter'){ e.preventDefault(); L.q = q.value; ascLookRun(v, 0); } };
  box.querySelector('#ascNat').onchange = e => { L.national = e.target.checked; ascLookShow(v); };
  await ascLookRun(v, 0);
}

async function ascLookRun(v, offset){
  const A = state.calc.asc, L = A.look, out = v.querySelector('#ascLookOut');
  if(!out || !L) return;
  const n = (A.lookSeq = (A.lookSeq || 0) + 1);
  if(!offset) out.innerHTML = `<div class="note">Looking up…</div>`;
  // whole codes only: a search cut inside a code would look up the start of that code
  const ask = fit => api('/api/activity/asc_rates?' + qs({q: fit.q, state: L.state, county: L.county, offset, limit: ASC_LOOK_PAGE}));
  let r, fit = AscModel.fitQuery(L.q, L.narrow ? ASC_Q_MAX : ASC_Q_WIDE, ASC_Q_CODES);
  try {
    try { r = await ask(fit); }
    catch(e){
      if(e.status !== 422 || L.narrow || fit.q.length <= ASC_Q_MAX) throw e;
      L.narrow = true;                                      // this server takes the shorter search only
      fit = AscModel.fitQuery(L.q, ASC_Q_MAX, ASC_Q_CODES);
      r = await ask(fit);
    }
  }
  catch(e){ if(n === A.lookSeq) out.innerHTML = `<div class="note">${esc(e.message)}</div>`; return; }
  if(n !== A.lookSeq || !out.isConnected) return;          // a later search, or another view, came first
  L.res = r; L.left = fit.left;
  L.rows = offset ? (L.rows || []).concat(r.rows) : r.rows;
  ascLookShow(v);
}

function ascLookShow(v){
  const A = state.calc.asc, L = A.look, out = v.querySelector('#ascLookOut');
  if(!out || !L || !L.res) return;
  const r = L.res, p = r.place, nat = !!L.national, ry = r.rule_year, py = r.prior_year;
  const defs = (A.places || {}).pi || {};
  const amt = (a, k) => a ? money2(a[k]) : '–';
  const six = ['total', 'medicare', 'copay', 'seq', 'medicare_after', 'total_after'];
  const sixHead = `<th class="num">Total</th><th class="num">Medicare</th><th class="num">Copay</th><th class="num">2% sequestration</th><th class="num">Medicare after it</th><th class="num">Total after it</th>`;
  const where = p ? `${ascTitle(p.county)}, ${p.state}` : 'No county';
  const head = `<thead><tr><th colspan="5"></th><th colspan="6" style="text-align:center">${esc(where)} (${esc(ry)})</th>
      ${nat ? `<th colspan="6" style="text-align:center">National (${esc(ry)})</th>` : `<th></th>`}<th colspan="2" style="text-align:center">Against ${esc(py)}</th></tr>
    <tr><th>Code</th><th>APC</th><th>Indicator</th><th>Discounted</th><th>New or revised</th>${sixHead}${nat ? sixHead : '<th class="num">National total</th>'}<th class="num">${esc(py)} national</th><th class="num">Change</th></tr></thead>`;
  const rows = (L.rows || []).map(x => {
    if(!x.listed) return `<tr><td>${esc(x.code)}</td><td>${esc(x.apc || '')}</td><td colspan="${nat ? 15 : 10}" class="muted">Not on the ${esc(ry)} addenda${x.prior ? ` (${esc(py)}: ${esc(x.prior.pi)}${x.prior.rate != null ? ', ' + money2(x.prior.rate) : ''})` : ''}</td><td></td><td></td></tr>`;
    const ind = `<span title="${esc(defs[x.pi] || '')}">${esc(x.pi)}</span>${x.adjusted ? '' : ' <span class="muted" title="Not wage-adjusted">**</span>'}`;
    const loc = x.local || null;
    return `<tr><td>${esc(x.code)}</td><td>${esc(x.apc || '')}</td><td>${ind}</td><td>${x.mpd === 'Y' ? 'yes' : x.mpd === 'N' ? 'no' : ''}</td><td>${esc(x.status || '')}${x.ci ? `<span class="sub">CI ${esc(x.ci)}</span>` : ''}</td>
      ${six.map(k => `<td class="num">${amt(loc, k)}</td>`).join('')}
      ${nat ? six.map(k => `<td class="num">${amt(x.national, k)}</td>`).join('') : `<td class="num">${amt(x.national, 'total')}</td>`}
      <td class="num">${x.prior && x.prior.rate != null ? money2(x.prior.rate) : '–'}</td>
      <td class="num">${x.change != null ? ascSigned2(x.change) : '–'}<span class="sub">${x.change_pct != null ? ((x.change_pct >= 0 ? '+' : '−') + Math.abs(x.change_pct * 100).toFixed(1) + '%') : ''}</span></td></tr>`;
  });
  const shown = (L.rows || []).length;
  out.innerHTML = `
    <div class="small">${p ? `${esc(where)}: ${p.cbsa_name ? `CBSA ${esc(p.cbsa)} ${esc(p.cbsa_name)}, ` : ''}ASC wage index ${esc(p.wi)} (${esc(ry)})${p.prior_wi ? `, ${esc(p.prior_wi)} in ${esc(py)}` : ''}.` : ''}
      ${r.total.toLocaleString()} code${r.total === 1 ? '' : 's'}${shown < r.total ? `, the first ${shown.toLocaleString()} shown` : ''}. Half of each amount is wage-adjusted; codes marked ** are not (drugs, brachytherapy sources, pass-through devices, skin substitute supplies). The copayment is not reduced by sequestration.${r.source ? ` Rates: ${esc(r.source)}.` : ''}</div>
    ${L.left ? `<div class="note">The search was too long: the last ${L.left === 1 ? 'code' : L.left.toLocaleString() + ' codes'} in it ${L.left === 1 ? 'was' : 'were'} left out. Look ${L.left === 1 ? 'it' : 'them'} up in a second search.</div>` : ''}
    <div style="overflow-x:auto;margin-top:6px"><table class="mini">${head}<tbody>${rows.join('') || `<tr><td colspan="19" class="muted">No code matches.</td></tr>`}</tbody></table></div>
    ${shown < r.total ? `<button class="ghost" id="ascMore" style="margin-top:6px">Show the next ${Math.min(ASC_LOOK_PAGE, r.total - shown)}</button>` : ''}`;
  const more = out.querySelector('#ascMore');
  if(more) more.onclick = () => { more.disabled = true; ascLookRun(v, shown); };
}
