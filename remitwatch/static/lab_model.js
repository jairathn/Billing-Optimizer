/* The strategy lab's arithmetic, on top of the calculator's (calc_model.js). Pure functions; runs in the browser and,
   for the tests, in node.

   A plan from the calculator is the starting point. Each strategy is a small module: it may change the plan (the
   calculator then prices it as usual) and may add what the calculator cannot see (an extra visit, an ASC's facility
   fee, an APP's visits, one-time cash). Strategies stack in a fixed order; each one's effect is measured against the
   plan as the strategies before it left it. The practice's own figures (minutes per service, costs, pay plans) are
   entered by the owner; where one is missing, what depends on it is shown as not entered rather than guessed. */
(function (root) {
  'use strict';
  const CM = (typeof module !== 'undefined' && module.exports) ? require('./calc_model.js') : root.CalcModel;

  // ------------------------------------------------------------------ the practice's own figures
  const MINUTES = [
    ['mohs_case', 'A Mohs case: first stage, from bringing the patient in to reading the slides'],
    ['mohs_stage', 'Each extra stage'],
    ['flap_graft', 'A flap or graft closure'],
    ['layered', 'A layered (intermediate or complex) closure'],
    ['excision', 'An excision'],
    ['visit_new', 'A new-patient visit'],
    ['visit_est', 'An established visit'],
    ['biopsy', 'Each biopsy'],
    ['ak_patient', 'Freezing AKs, one patient'],
    ['benign_patient', 'Freezing benign lesions, one patient'],
    ['turnover', 'Rooming and turnover for an extra visit'],
  ];
  const COSTS = [
    ['per_clinic_day', 'A clinic day: staff, rent and overhead that run whether patients come or not'],
    ['per_encounter', 'Each patient seen: check-in, rooming, supplies'],
    ['per_mohs_case', 'Each Mohs case: histology, lab'],
    ['per_stage', 'Each extra stage'],
    ['per_closure', 'Each flap, graft or layered closure'],
    ['per_excision', 'Each excision'],
    ['per_biopsy', 'Each biopsy (supplies, and outside pathology the practice pays for)'],
    ['asc_per_case', "A case in the ASC (a closure, or an excision with its repair): the ASC's own cost, all in (staff, supplies, room)"],
    ['office_per_case_saved', 'What the clinic saves when a closure or an excision goes to the ASC'],
    ['app_per_visit', 'What an APP visit costs the practice (pay, benefits, room)'],
  ];
  const COMP_TYPES = [['pct', 'A share of the collections for their own production'], ['wrvu', 'A rate per work RVU'], ['base', 'A base, plus a share above a threshold']];

  const MAX_SCENARIOS = 200;
  const ID = /^[A-Za-z0-9:_-]{1,60}$/;

  function blankAssumptions() {
    return { minutes: Object.fromEntries(MINUTES.map(([k]) => [k, null])), costs: Object.fromEntries(COSTS.map(([k]) => [k, null])),
             session_minutes: null, comp: {}, scenarios: [] };
  }
  // The saved figures as the page may use and save them, whatever came back from the store: each figure a number of 0 or
  // more (else not entered), pay plans of a known kind, scenarios with a name, a clinician and plain settings. Anything
  // else is dropped, so a value that is not a number can never reach the page as one. The server checks the same.
  const MAX_FIGURE = 1e9;             // as the server's
  const fig = v => (typeof v === 'number' && isFinite(v) && v >= 0 && v <= MAX_FIGURE) ? v : null;
  const share01 = v => (fig(v) != null && v <= 1) ? v : null;
  const isObj = v => !!v && typeof v === 'object' && !Array.isArray(v);
  function cleanPlan(p) {
    if (!isObj(p) || !COMP_TYPES.some(([k]) => k === p.type)) return null;
    return { type: p.type, pct: share01(p.pct), per_wrvu: fig(p.per_wrvu), base: fig(p.base), threshold: fig(p.threshold), above_pct: share01(p.above_pct) };
  }
  // a plan's settings as saved: numbers, true or false, short text, and lists and objects of those, a few levels deep
  function plain(v, depth) {
    if (v === null || typeof v === 'boolean') return v;
    if (typeof v === 'number') return isFinite(v) ? v : undefined;
    if (typeof v === 'string') return v.length <= 200 ? v : undefined;
    if (!(depth > 0) || !v || typeof v !== 'object') return undefined;
    if (Array.isArray(v)) return v.length <= 100 ? v.map(w => plain(w, depth - 1)).filter(w => w !== undefined) : undefined;
    const o = {};
    for (const [k, w] of Object.entries(v)) {
      if (k.length > 120 || k === '__proto__') continue;
      const c = plain(w, depth - 1);
      if (c !== undefined) o[k] = c;
    }
    return o;
  }
  function cleanScenario(s) {
    if (!isObj(s)) return null;
    const name = typeof s.name === 'string' ? s.name.trim().slice(0, 100) : '';
    if (!name || typeof s.who !== 'string' || !ID.test(s.who)) return null;
    const x = plain(s.x, 3), chosen = {};
    for (const [id, c] of Object.entries(isObj(s.chosen) ? s.chosen : {})) {
      if (!STRATEGIES.some(S => S.id === id) || !isObj(c)) continue;
      const params = {};
      for (const [k, v] of Object.entries(isObj(c.params) ? c.params : {})) if (/^[a-z_]{1,40}$/.test(k) && typeof v === 'number' && isFinite(v)) params[k] = v;
      chosen[id] = { on: c.on === true, params };
    }
    return { name, who: s.who, x: isObj(x) ? x : {}, chosen, saved_at: typeof s.saved_at === 'string' ? s.saved_at.slice(0, 40) : '' };
  }
  function clean(A) {
    A = isObj(A) ? A : {};
    const pick = (src, keys) => {
      const o = {};
      if (isObj(src)) for (const [k] of keys) if (Object.prototype.hasOwnProperty.call(src, k)) o[k] = fig(src[k]);
      return o;
    };
    const comp = {};
    for (const [id, p] of Object.entries(isObj(A.comp) ? A.comp : {})) { const c = ID.test(id) ? cleanPlan(p) : null; if (c) comp[id] = c; }
    const scenarios = (Array.isArray(A.scenarios) ? A.scenarios : []).map(cleanScenario).filter(Boolean).slice(0, MAX_SCENARIOS);
    return { minutes: pick(A.minutes, MINUTES), costs: pick(A.costs, COSTS), session_minutes: fig(A.session_minutes), comp, scenarios };
  }
  function filled(A) {
    const b = blankAssumptions(), c = clean(A);
    return { minutes: Object.assign(b.minutes, c.minutes), costs: Object.assign(b.costs, c.costs), session_minutes: c.session_minutes,
             comp: c.comp, scenarios: c.scenarios };
  }
  const num = v => (v == null || v === '' || !isFinite(+v)) ? null : +v;

  // ------------------------------------------------------------------ what a clinic day holds
  function activity(d, x) {
    const U = CM.units(d, x), L = d.levers;
    const sum = codes => codes.reduce((t, c) => t + (U[c] || 0), 0);
    const mohs = sum(['17311', '17313']), stages = sum(['17312', '17314']);
    const fg = sum(Object.keys(L.flap_graft.mix)), lay = sum(Object.keys(L.layered.mix)), exc = sum(Object.keys(L.excision.mix));
    const vn = sum(['99202', '99203', '99204', '99205']), ve = sum(['99211', '99212', '99213', '99214', '99215']);
    const bx = Math.max(0, x.bx_patients) * Math.max(1, x.bx_per_patient || 1);
    const ak = CM.akPatients(d, x).patients, ben = Math.max(0, x.benign);
    return { mohs, stages, fg, lay, exc, vn, ve, bx, ak, ben, encounters: vn + ve + mohs + exc };
  }

  function minutes(act, A) {
    const m = A.minutes, missing = new Set();
    const part = (n, key) => { if (!(n > 0)) return 0; const v = num(m[key]); if (v == null) { missing.add(key); return 0; } return n * v; };
    const total = part(act.mohs, 'mohs_case') + part(act.stages, 'mohs_stage') + part(act.fg, 'flap_graft') + part(act.lay, 'layered')
      + part(act.exc, 'excision') + part(act.vn, 'visit_new') + part(act.ve, 'visit_est') + part(act.bx, 'biopsy')
      + part(act.ak, 'ak_patient') + part(act.ben, 'benign_patient');
    return { total, missing: [...missing] };
  }

  function costs(act, A, daysYear) {
    const c = A.costs, missing = new Set();
    const part = (n, key) => { if (!(n > 0)) return 0; const v = num(c[key]); if (v == null) { missing.add(key); return 0; } return n * v; };
    const perDay = part(act.encounters, 'per_encounter') + part(act.mohs, 'per_mohs_case') + part(act.stages, 'per_stage')
      + part(act.fg + act.lay, 'per_closure') + part(act.exc, 'per_excision') + part(act.bx, 'per_biopsy');
    const fixed = part(1, 'per_clinic_day') * daysYear;
    return { variable: perDay * daysYear * 100, fixed: fixed * 100, total: (perDay * daysYear + fixed) * 100, missing: [...missing] };   // cents
  }

  // work RVUs a year for the clinician's own production (the practice's ancillary lines are not theirs)
  function wrvuTable(d, x) { const year = x.year || d.rate_year; return (d.work_rvu || {})[year] || (d.work_rvu || {})[d.rate_year] || {}; }
  function wrvus(d, x) {
    const U = CM.units(d, x), days = Math.max(0, x.days_per_week) * Math.max(0, x.weeks_per_year);
    const table = wrvuTable(d, x);
    const anc = new Set(CM.ANCILLARY), groups = (d.levers.other || {}).groups || {};
    let t = 0;
    for (const [c, n] of Object.entries(U)) if (!anc.has(groups[c]) && table[c]) t += n * table[c];
    return t * days;
  }

  // what a pay plan pays on a year's production (collections in cents)
  function pay(plan, collections, wrvu) {
    if (!plan || !plan.type) return null;
    if (plan.type === 'pct') return num(plan.pct) == null ? null : collections * plan.pct;
    if (plan.type === 'wrvu') return num(plan.per_wrvu) == null ? null : wrvu * plan.per_wrvu * 100;
    if (plan.type === 'base') {
      if (num(plan.base) == null) return null;
      return plan.base * 100 + Math.max(0, collections - (num(plan.threshold) || 0) * 100) * (num(plan.above_pct) || 0);
    }
    return null;
  }

  // the collections a pay target needs, under a plan (inverse of pay); 0 when a base plan's base already pays it
  function collectionsFor(plan, target) {
    if (!plan || !plan.type) return null;
    if (plan.type === 'pct') return plan.pct > 0 ? target / plan.pct : null;
    if (plan.type === 'base') {
      if (num(plan.base) == null) return null;
      const above = target - plan.base * 100;
      if (above <= 0) return 0;
      return plan.above_pct > 0 ? (num(plan.threshold) || 0) * 100 + above / plan.above_pct : null;
    }
    return null;           // a wRVU plan targets wRVUs, not collections
  }
  // what a plan still needs before a pay target can be worked out from it, in words (null when nothing is missing)
  function planMissing(plan, target) {
    if (!plan || !plan.type) return 'a pay plan';
    if (plan.type === 'pct') return plan.pct > 0 ? null : 'the share of collections';
    if (plan.type === 'wrvu') return plan.per_wrvu > 0 ? null : 'the rate per work RVU';
    if (plan.type === 'base') {
      if (num(plan.base) == null) return 'the base';
      if (target != null && target <= plan.base * 100) return null;
      return plan.above_pct > 0 ? null : 'the share above the threshold';
    }
    return 'a pay plan';
  }

  // ------------------------------------------------------------------ helpers the strategies share
  const BIGGER = c => ['14001', '14021', '14041', '14061', '14301', '14302', '14350'].includes(c) || (/^15\d{3}$/.test(c) && (+c <= 15261 || (+c >= 15570 && +c <= 15770)));
  function kindShare(d, family, kinds) {
    const mix = d.mix[family] || d.mix._overall || [];
    return mix.reduce((t, [g, s]) => t + (kinds.includes(CM.payerKind(d, g)) ? s : 0), 0);
  }
  const clamp = (v, a, b) => Math.min(b, Math.max(a, +v || 0));
  // what a patient slot brings in a year's collections terms: allowed per patient seen, times the plan's collected share
  function slotValue(d, x, cal) {
    const per = (d.schedule || {}).allowed_per_patient;
    if (!per) return 0;
    const ev = CM.evaluate(d, x, cal);
    return per * (ev.production.allowed > 0 ? ev.production.collections / ev.production.allowed : 1);
  }

  // the share of the flaps and grafts paired with a Mohs first stage (one closure a case at most, as the calculator pairs them)
  function pairedShare(x) { const R = clamp(x.fg_share, 0, 2), T = R + clamp(x.lay_mohs, 0, 2); return T > 0 ? Math.min(1, T) / T : 0; }
  const COINSURANCE = 0.2;           // Medicare Part B
  // what a Medicare patient pays more, each, when the plan moves from x to y: 20% of the higher allowed amount
  function medicareCoinsurance(d, x, y, cal, patientsYear) {
    if (!(patientsYear > 0)) return null;
    const a = CM.evaluate(d, x, cal), b = CM.evaluate(d, y, cal);
    if (!a.byPayer || !b.byPayer) return null;
    let gain = 0;
    for (const g of new Set([...Object.keys(a.byPayer), ...Object.keys(b.byPayer)])) {
      if (CM.payerKind(d, g) === 'medicare') gain += ((b.byPayer[g] || {}).allowed || 0) - ((a.byPayer[g] || {}).allowed || 0);
    }
    return COINSURANCE * gain / patientsYear;
  }

  // ------------------------------------------------------------------ the ASC (an ambulatory surgery center at Newburgh)
  // Mohs (17311-17315) is billed in the clinic only, at the office amount: it is never on an ASC claim and never earns an
  // ASC fee, whatever CMS's ASC list allows. What may move: the closure after a clinic Mohs (the same day, or a later day)
  // and a physician's excision with its repair. The payload (calculator.py) gives the ASC's rates by year (d.asc[year]:
  // [payment indicator, subject to discounting, {office: wage-adjusted cents}]), and in d.asc: the site, Medicare's Indiana
  // amounts [office, facility] (medicare), the codes the multiple-procedure rule ranks (mppr) and the add-ons (add_on),
  // the ASCQR factor, and the cases counted over the window (cases).
  const ASC_MOHS = ['17311', '17312', '17313', '17314', '17315'];
  const ASC_TYPES = [['flap', 'Flap'], ['graft', 'Full-thickness graft'], ['complex', 'Complex repair'], ['intermediate', 'Intermediate repair'],
                     ['other', 'Other closure (split graft, skin substitute, simple repair)']];
  const ASC_LOW_MARGIN = ['intermediate', 'complex'];     // closures whose ASC fee is small (P3 codes, 13131/13132)
  const ASC_KINDS = ['medicare', 'medicare_advantage', 'commercial'];     // Medicaid and other payers' patients are not moved
  const ASCQR_CODES = ['A2', 'G2', 'P2'];
  // as remitwatch.activity.closure_type
  function closureType(c) {
    if (!/^\d{5}$/.test(c)) return null;
    const n = +c;
    if (n >= 12031 && n <= 12057) return 'intermediate';
    if (n >= 13100 && n <= 13153) return 'complex';
    if ((n >= 14000 && n <= 14061) || n === 14301 || n === 14302) return 'flap';
    if (n >= 15200 && n <= 15261) return 'graft';
    if ((n >= 12001 && n <= 12021) || n === 13160 || n === 14350 || (n >= 15040 && n <= 15157) || (n >= 15271 && n <= 15278) || (n >= 15570 && n <= 15770)) return 'other';
    return null;
  }
  // 'code' or 'codexunits', space-separated, as the payload carries a case
  function caseCodes(s) { return String(s || '').split(/\s+/).filter(Boolean).map(t => { const [c, u] = t.split('x'); return [c, u ? +u : 1]; }); }
  // the closure a case is known by: graft, then flap, complex, intermediate, other
  function caseLabel(codes) {
    const types = new Set(caseCodes(codes).map(([c]) => closureType(c)));
    return ['graft', 'flap', 'complex', 'intermediate', 'other'].find(t => types.has(t)) || 'other';
  }
  // Medicare's amount for one unit in Indiana, [office, facility], physician (only a physician operates in an ASC)
  function ascMedicare(d, c, Y) { const r = (((d.asc || {}).medicare || {})[c] || {})[Y]; return r && r[0] != null ? r : null; }

  // One physician, one patient, one day (Claims Processing Manual ch. 12 sec. 40.6): each line at its own setting's
  // amount (the clinic's Mohs at the office amount, the ASC's closure at the facility amount, on separate claims); the
  // codes the multiple-procedure rule ranks, highest first: the first in full, the others at half; add-ons never reduced.
  // lines: [{c, a (cents a unit), u (units)}]; returns what each line is paid
  function dayPro(d, lines) {
    const rho = d.constants.mppr_share, ranked = new Set([...(((d.asc || {}).mppr) || []), ...(d.mppr || [])]);
    const paid = lines.map(l => ranked.has(l.c) ? 0 : l.a * l.u), units = [];
    lines.forEach((l, i) => { if (ranked.has(l.c)) for (let k = 0; k < Math.max(1, Math.round(l.u)); k++) units.push([l.a, i]); });
    units.sort((p, q) => q[0] - p[0]);
    units.forEach(([a, i], k) => { paid[i] += k === 0 ? a : Math.round(a * rho); });
    return paid;
  }

  // What Medicare pays the ASC for one claim (42 CFR 416.172): Addendum AA's rate, wage-adjusted for the ASC's office;
  // in full when it is the claim's one procedure; with more than one, the highest in full and every other one subject to
  // discounting at half; packaged codes (N1: add-ons, extra stages) nothing. Never a Mohs code. null when a code is not on
  // the ASC list (the case cannot be done there). ascqr: the ASC fails quality reporting (A2, G2 and P2 codes lower).
  function ascFee(d, Y, codes, ascqr) {
    const S = d.asc || {}, T = S[Y] || {}, office = (S.site || {}).office || 'newburgh';
    const f = ascqr ? ((S.ascqr || {})[Y] || 1) : 1, rows = [];
    for (const [c, u] of codes) {
      if (ASC_MOHS.includes(c)) continue;
      const r = T[c];
      if (!r) return null;
      if (r[0] === 'N1') continue;
      let a = (r[2] || {})[office];
      if (a == null) return null;
      if (ascqr && ASCQR_CODES.includes(r[0])) a = Math.round(a * f);
      for (let k = 0; k < Math.max(1, Math.round(u)); k++) rows.push([a, !!r[1]]);
    }
    rows.sort((p, q) => q[0] - p[0]);
    return rows.reduce((t, [a, disc], i) => t + (i === 0 || !disc ? a : Math.round(a * 0.5)), 0);
  }

  // One case, in cents of allowed. what 'mohs': codes are the first stage(s) and the closure; 'excision': the excision(s)
  // and the repair. Returns A (all in the clinic, the same day), B (the closure, or the whole excision case, in the ASC the
  // same day), C (the closure in the ASC on a later day: two days, each with its own ranking), fee (the ASC's, for B and
  // C alike), or {ok: false, missing} when a code has no price. opts:
  //   stages      extra Mohs stages a case (in the clinic in every option, never reduced)
  //   cross       the payer cuts the second procedure across the clinic and ASC claims (Medicare does; default true)
  //   em          share of cases with an office visit the 2027 same-day rule cuts (20% off all but the highest service)
  //   emAsc       that rule also reaches the ASC claim's lines (CMS has not said; default no)
  //   cutAddOns   that rule cuts add-on lines too (as the calculator's x.cut_add_ons)
  //   ascqr       the ASC fails quality reporting
  //   price       (code, 'office'|'asc') -> cents a unit; default Medicare's Indiana amounts
  function ascCase(d, Y, what, codes, opts) {
    opts = opts || {};
    const price = opts.price || ((c, site) => { const m = ascMedicare(d, c, Y); return m ? m[site === 'asc' ? 1 : 0] : null; });
    const parsed = caseCodes(codes), lines = [], missing = [];
    for (const [c, u] of parsed) {
      const mohs = ASC_MOHS.includes(c), nf = price(c, 'office'), f = mohs ? nf : price(c, 'asc');
      if (nf == null || f == null) missing.push(c);
      lines.push({ c, u, nf, f, mohs });
    }
    if (what === 'mohs' && opts.stages > 0) {
      const sc = parsed.some(([c]) => c === '17311') ? '17312' : '17314', a = price(sc, 'office');
      if (a == null) missing.push(sc); else lines.push({ c: sc, u: opts.stages, nf: a, f: a, mohs: true });
    }
    const fee = missing.length ? null : ascFee(d, Y, parsed, !!opts.ascqr);
    if (missing.length || fee == null) return { ok: false, missing: missing.length ? missing : parsed.map(([c]) => c).filter(c => !ASC_MOHS.includes(c)) };
    const addOn = new Set([...(((d.asc || {}).add_on) || []), ...(d.add_on || [])]);
    const sum = v => v.reduce((t, w) => t + w, 0);
    // the 2027 same-day rule: 20% off the lines it reaches, all but the highest of them
    const cut = (ls, paid, reach) => {
      const idx = ls.map((l, i) => i).filter(i => reach(ls[i]));
      if (idx.length < 2) return 0;
      const top = Math.max(...idx.map(i => paid[i]));
      const cutOf = idx.filter(i => opts.cutAddOns !== false || !addOn.has(ls[i].c)).reduce((t, i) => t + paid[i], 0);
      const topIn = idx.some(i => paid[i] === top && (opts.cutAddOns !== false || !addOn.has(ls[i].c)));
      return 0.2 * (cutOf - (topIn ? top : 0));
    };
    const em = Math.max(0, +opts.em || 0);
    const at = asc => lines.map(l => ({ c: l.c, u: l.u, a: asc && !l.mohs ? l.f : l.nf, mohs: l.mohs }));
    const clinic = at(false), moved = at(true);
    const pA = dayPro(d, clinic);
    const A = sum(pA) - (what === 'mohs' ? em * cut(clinic, pA, () => true) : 0);
    if (what !== 'mohs') { const B = sum(dayPro(d, moved)); return { ok: true, A, B, C: B, fee }; }
    // the same day: one ranking across both claims, or (a payer that does not cut across claims) one each
    const apart = ls => { const paid = new Array(ls.length).fill(0), grp = [ls.map((l, i) => i).filter(i => ls[i].mohs), ls.map((l, i) => i).filter(i => !ls[i].mohs)];
                          for (const g of grp) dayPro(d, g.map(i => ls[i])).forEach((v, k) => { paid[g[k]] = v; }); return paid; };
    const pB = opts.cross === false ? apart(moved) : dayPro(d, moved);
    const B = sum(pB) - em * cut(moved, pB, opts.emAsc ? (() => true) : (l => l.mohs));
    const pC = apart(moved);
    const C = sum(pC) - em * cut(moved, pC, l => l.mohs);       // the closure's own day has no 0-day procedure
    return { ok: true, A, B, C, fee };
  }

  // the ASC settings the strategies share (the 'asc' strategy's, else its defaults)
  function ascPlan(p) {
    const n = v => (v == null || v === '' || !isFinite(+v)) ? null : +v;
    return { to_asc: clamp(p.to_asc, 0, 1), to_clinic: clamp(p.to_clinic, 0, 1), flap_graft: !!p.flap_graft, layered: !!p.layered,
             excisions: clamp(p.excisions, 0, 1), own: n(p.own) == null ? null : clamp(p.own, 0, 1),
             commercial_pct: n(p.commercial_pct) == null ? null : clamp(p.commercial_pct, 0, 3),
             ma_cross: p.ma_cross == null ? true : !!p.ma_cross, ascqr: !!p.ascqr, em_rule: !!p.em_rule };
  }
  // cases counted at the ASC's office (an ASC strategy has something to work on)
  function ascHere(d, whats) {
    const S = d.asc || {}, C = S.cases || {}, st = (S.site || {}).state || 'IN';
    return whats.some(w => (C[w] || []).some(r => r[3] === st && r[4] > 0));
  }
  const ascInScope = (plan, type) => (['flap', 'graft', 'other'].includes(type) ? plan.flap_graft : plan.layered);

  // Every case the payload counted at the ASC's office, a year on this plan, each priced (ascCase) at its payer:
  // Medicare at Indiana's amounts; Medicare Advantage and commercial plans at their own share of Medicare (their usual
  // rates over Medicare's, across the plans of that kind that pay this clinician's Mohs) and, for the ASC's fee, at the
  // share of Medicare's ASC rates entered (feeShare null until it is). Volumes follow the plan: a type of case scales with
  // the plan's Mohs cases and closures (or excisions and their repairs) against those measured.
  function ascBook(d, x, plan) {
    const Y = x.year || d.rate_year, S = d.asc || {}, C = S.cases || {}, L = d.levers;
    const st = (S.site || {}).state || 'IN', cd = +C.clinic_days || 0;
    const out = { Y, rows: [], ok: !!(S[Y] && S.medicare && C.mohs && cd > 0), from: C.from || null };
    if (!out.ok) return out;
    const days = Math.max(0, x.days_per_week) * Math.max(0, x.weeks_per_year);
    const sc = (a, b) => (b > 0 ? Math.max(0, +a || 0) / b : 1);
    const sM = sc(x.mohs_cases, L.mohs.cases), sFG = sM * sc(x.fg_share, L.flap_graft.share), sLay = sM * sc(x.lay_mohs, L.layered.mohs_share);
    const scale = { flap: sFG, graft: sFG, other: sM, complex: sLay, intermediate: sLay,
                    excision: sc(x.excisions, L.excision.per_day) * sc(x.lay_exc, L.layered.excision_share) };
    const next = d.next_year && Y === d.next_year;
    const kindGroups = {};
    for (const k of ASC_KINDS) {
      const gs = CM.mixOf(d, '17311').filter(([g]) => CM.payerKind(d, g) === k);
      const anyG = Object.keys(d.payers || {}).find(g => CM.payerKind(d, g) === k);
      kindGroups[k] = { gs: gs.length ? gs : (anyG ? [[anyG, 1]] : []) };
    }
    // a plan's usual rate over Medicare's for a code (its own rates, weighted by its share of this clinician's Mohs)
    const ratioMemo = {};
    const ratio = (k, c) => {
      const key = k + '|' + c;
      if (key in ratioMemo) return ratioMemo[key];
      let t = 0, w = 0;
      for (const [g, s] of kindGroups[k].gs) {
        const m = (d.medicare[c] || {})[Y], pr = m ? CM.price(d, x, c, g, Y, true) : null;
        if (pr != null && m) { t += s * pr / m; w += s; }
      }
      return (ratioMemo[key] = w > 0 ? t / w : (c === '17311' ? 1 : ratio(k, '17311')));
    };
    const coll = (k, c) => {
      const gs = kindGroups[k].gs;
      if (!gs.length) return 1;
      let t = 0, w = 0;
      for (const [g, s] of gs) { t += s * CM.collectShare(d, x, g, c); w += s; }
      return w > 0 ? t / w : 1;
    };
    const follows = k => k === 'medicare' || (k === 'medicare_advantage' && kindGroups[k].gs.some(([g]) => CM.followsMedicare(d, x, g)));
    const emShare = next ? clamp(CM.mohsVisitShare(d, x), 0, 1) : 0;
    const book = (what, codes, site, kind, state, n, tail) => {
      const type = what === 'mohs' ? caseLabel(codes) : 'excision';
      if (state !== st || !(n > 0)) return { what, site, kind, n, type, priced: false };          // seen in another state
      const perYear = n / cd * days * (scale[type] != null ? scale[type] : 1);
      if (!ASC_KINDS.includes(kind)) return { what, site, kind, n, type, perYear, priced: false };  // Medicaid, other payers: not moved
      const med = kind === 'medicare';
      const price = med ? null : (c, where) => { const m = ascMedicare(d, c, Y); return m ? m[where === 'asc' ? 1 : 0] * ratio(kind, c) : null; };
      const lead = caseCodes(codes).find(([c]) => closureType(c)) || ['17311'];
      const v = ascCase(d, Y, what, codes, { stages: what === 'mohs' ? (+tail || 0) / n : 0, cross: med ? true : plan.ma_cross,
                                             em: follows(kind) ? emShare : 0, emAsc: plan.em_rule, cutAddOns: x.cut_add_ons !== false,
                                             ascqr: plan.ascqr, price });
      return { what, codes, site, kind, n, type, perYear, v, priced: v.ok,
               physician: what === 'excision' ? !!tail : d.tier !== 'nonphysician', coll: coll(kind, lead[0]),
               feeShare: med ? 1 : plan.commercial_pct, medicare: med };
    };
    for (const [codes, site, kind, state, n, stages] of C.mohs || []) out.rows.push(book('mohs', codes, site, kind, state, n, stages));
    for (const [codes, site, kind, state, n, phys] of C.excision || []) out.rows.push(book('excision', codes, site, kind, state, n, phys));
    out.emShare = emShare;
    return out;
  }

  // What moving cases is worth a year (collections, cents), with the facility fee apart: dPro the professional fees (the
  // surgeon's own production), dFee the ASC's fees at the plan's share of them before the practice's share (own). Also what
  // the routing the payload found is worth against closing every same-day case in the clinic (current), the cases by type,
  // and Medicare's allowed amounts for the per-case figures.
  function ascTotals(d, x, plan) {
    const B = ascBook(d, x, plan);
    const T = { book: B, dPro: 0, dFee: 0, moved: 0, back: 0, exc: 0, coins: 0, coinsN: 0, coinsBack: 0,
                current: { n: 0, pro: 0, fee: 0, allowed: { n: 0, A: 0, B: 0, fee: 0 } },
                pool: { n: 0, priced: 0, medicare: 0, asc: 0, clinic: 0 }, split: 0, types: {} };
    if (!B.ok) return T;
    for (const r of B.rows) {
      if (r.what === 'mohs' && r.perYear != null) {
        T.pool.n += r.perYear;
        if (r.site === 'asc') T.pool.asc += r.perYear; else if (r.site === 'clinic') T.pool.clinic += r.perYear;
        // per type: the cases a year at every payer, by where they are closed now
        const t = T.types[r.type] = T.types[r.type] || { n: 0, asc: 0, clinic: 0, med: 0, A: 0, B: 0, C: 0, fee: 0 };
        t.n += r.perYear;
        if (r.site === 'asc' || r.site === 'clinic') t[r.site] += r.perYear;
      }
      if (!r.priced) continue;
      const known = r.feeShare != null, v = r.v;
      if (r.what === 'mohs' && known) T.pool.priced += r.perYear;
      if (r.what === 'mohs' && r.medicare) T.pool.medicare += r.perYear;
      // ...and Medicare's allowed amounts a case, weighted by its Medicare cases a year (both sites)
      if (r.what === 'mohs' && r.medicare && (r.site === 'asc' || r.site === 'clinic')) {
        const t = T.types[r.type];
        t.med += r.perYear; t.A += r.perYear * v.A; t.B += r.perYear * v.B; t.C += r.perYear * v.C; t.fee += r.perYear * v.fee;
      }
      if (!known) continue;
      const fee = v.fee * r.feeShare;
      if (r.what === 'mohs') {
        if (r.site === 'asc') {              // the routing found: worth this much against the clinic
          T.current.n += r.perYear; T.current.pro += r.perYear * (v.B - v.A) * r.coll; T.current.fee += r.perYear * fee * r.coll;
          if (r.medicare) { const a = T.current.allowed; a.n += r.perYear; a.A += r.perYear * v.A; a.B += r.perYear * v.B; a.fee += r.perYear * v.fee; }
        }
        if (r.site === 'both') { T.split += r.perYear; continue; }
        if (!ascInScope(plan, r.type)) continue;
        const m = r.site === 'clinic' ? plan.to_asc : -plan.to_clinic;
        if (!m) continue;
        const k = r.perYear * m;
        if (m > 0) T.moved += k; else T.back -= k;
        T.dPro += k * (v.B - v.A) * r.coll; T.dFee += k * fee * r.coll;
        if (r.medicare && k > 0) { T.coins += k * COINSURANCE * (v.B + v.fee - v.A); T.coinsN += k; }
        if (r.medicare && k < 0) T.coinsBack -= k;
      } else {
        if (r.site === 'both') { T.split += r.perYear; continue; }
        if (r.site !== 'clinic' || !r.physician || !plan.excisions) continue;
        const k = r.perYear * plan.excisions;
        T.exc += k; T.dPro += k * (v.B - v.A) * r.coll; T.dFee += k * fee * r.coll;
        if (r.medicare) { T.coins += k * COINSURANCE * (v.B + v.fee - v.A); T.coinsN += k; }
      }
    }
    return T;
  }

  // ------------------------------------------------------------------ the strategies
  // params: [key, label, min, max, step, format, default]; apply returns {x, extra:{collections, minutes, encounters,
  // cost, one_time}, notes[], needs[]} where minutes and encounters are per clinic day and money is a year, in cents
  const STRATEGIES = [
    {
      id: 'stage', title: 'Stage the bigger closures to a later day', tier: 'high', whatif: true,
      applies: (d, x) => x.mohs_cases > 0 && Object.keys(d.levers.flap_graft.mix).length > 0,
      why: "When a flap or graft is done the same day as the Mohs first stage, Medicare's multiple-procedure rule pays the first stage at half. Done on another day, both are paid in full, at the cost of a second visit. A what-if: it is shown apart and never added to the plan's total.",
      compliance: "Stage a closure only when a clinical reason exists at the time (margins still pending, the reconstructive surgeon's availability, the patient's condition), written in the note when the decision is made. NCCI policy manual, ch. I: \"Physicians shall not inconvenience beneficiaries nor increase risks to beneficiaries by performing services on different dates of service to avoid MUE or NCCI PTP edits.\" Medicare counts as abuse practices that \"may directly or indirectly result in unnecessary Medicare Program costs\". CMS, in the 2027 final rule: \"we do not find it appropriate to schedule medical services for patients to maximize payment, which would create undue burden and potential medical risk for beneficiaries\"; it will look for the pattern with claims edits, comparative billing reports and medical review. A visit on the Mohs day before a next-day flap falls in the flap's 90-day global.",
      params: [['share', 'Share of the bigger closures done on a later day', 0, 1, 0.01, 'pct', 0.01],
               ['all', 'Count every flap and graft, not only the bigger ones (grafts, interpolation flaps, flaps over 10 sq cm)', 0, 1, 1, 'bool', 0],
               ['return_rate', 'Patients who come back for the closure (the rest heal open, with a wound-care visit)', 0, 1, 0.01, 'pct', 1],
               ['full', 'The schedule is full: each second visit takes a slot another patient would have had', 0, 1, 1, 'bool', 1]],
      apply(d, x, A, p, cal) {
        const L = d.levers, codes = Object.keys(L.flap_graft.mix).filter(c => p.all ? true : BIGGER(c));
        const share = clamp(p.share, 0, 1), back = clamp(p.return_rate, 0, 1);
        const U = CM.units(d, x), pair = pairedShare(x);
        // closures a clinic day moved off the first stage's day. On every one of those cases the first stage is paid in
        // full; the closure is billed in full on the later day when the patient comes back, and never done when not
        const moved = codes.reduce((t, c) => t + (U[c] || 0), 0) * pair * share;
        const returned = moved * back, lost = moved - returned;
        const y = Object.assign({}, x, { stage: { codes, share } });
        const days = x.days_per_week * x.weeks_per_year, m = A.minutes, c = A.costs;
        const turn = num(m.turnover), perEnc = num(c.per_encounter);
        const needs = [];
        if (moved > 0 && turn == null) needs.push('minutes.turnover');
        if (moved > 0 && perEnc == null) needs.push('costs.per_encounter');
        let lostColl = 0, lostW = 0, lostMin = 0, lostCost = 0;
        if (lost > 0) {
          // the lost closures at what the plan above bills for them once staged: in full
          const full = CM.evaluate(d, Object.assign({}, x, { fg_share: clamp(x.fg_share, 0, 2) * pair, lay_mohs: 0, stage: { codes, share: 1 } }), cal);
          const f = share * (1 - back), table = wrvuTable(d, x);
          for (const code of codes) {
            lostColl += ((full.byCode[code] || {}).collections || 0) * f;
            lostW += (U[code] || 0) * pair * f * (table[code] || 0) * days;
          }
          lostMin = lost * (num(m.flap_graft) || 0);
          lostCost = lost * (num(c.per_closure) || 0) * days * 100;
        }
        // every moved patient comes back once: for the closure, or for wound care
        const slot = p.full ? moved * days * slotValue(d, x, cal) : 0;
        const coins = moved > 0 ? medicareCoinsurance(d, x, y, cal, moved * days * kindShare(d, 'mohs', ['medicare'])) : null;
        const dollars = v => Math.round(v / 100).toLocaleString();
        return { x: y, extra: { collections: -slot - lostColl, wrvu: -lostW, minutes: moved * (turn || 0) - lostMin, encounters: moved,
                                cost: moved * (perEnc || 0) * days * 100 - lostCost },
                 notes: [`${returned.toFixed(2)} closures a clinic day move to a later visit (${codes.length} closure codes in scope)`
                   + (lost > 0 ? `; ${lost.toFixed(2)} are never done because the patient does not come back, about ${dollars(lostColl)} dollars a year of closures lost` : '')
                   + (slot ? `; the second visits take slots that would have brought about ${dollars(slot)} dollars a year` : '') + '.'
                   + (coins != null && coins > 0 ? ` A Medicare patient pays about ${dollars(coins)} dollars more in coinsurance (20% of the higher amount)` : ' Medicare patients pay 20% of what the first stage gains, as coinsurance')
                   + ', and makes a second trip with an open wound.'], needs };
      },
    },
    {
      id: 'other_surgeon', title: 'Same-day reconstruction by a surgeon of another specialty', tier: 'medium',
      applies: (d, x) => d.tier !== 'nonphysician' && x.mohs_cases > 0 && Object.keys(d.levers.flap_graft.mix).length > 0,
      why: "Medicare's multiple-procedure rule applies to one surgeon, or surgeons of the same specialty in a group. A flap or graft done the same day by a surgeon of another specialty (a facial plastic or oculoplastic surgeon, say) leaves the Mohs first stage paid in full, with no second visit. The closure is then that surgeon's.",
      compliance: "Claims Processing Manual ch. 12 sec. 40.8.B: when surgeons of different specialties each perform a different procedure, neither co-surgery nor multiple-surgery rules apply. Sec. 40.6.A defines multiple surgery as procedures by one physician or physicians in the same group, and says nothing of specialty: confirm with the MAC how specialty applies to the group's claims before counting on it. The hire must be bona fide, not made to avoid the reduction, and each referral clinically justified and documented (the MAC's Mohs article asks why a plastic surgeon was used). It needs a physician of another specialty: a PA or NP closing the defect does not count.",
      params: [['share', 'Share of flap and graft closures done by that surgeon', 0, 1, 0.05, 'pct', 0.2],
               ['employed', 'The surgeon is employed by the practice (the closure stays practice revenue)', 0, 1, 1, 'bool', 1],
               ['cost', "What the surgeon costs a year, dollars", 0, 1000000, 5000, 'usd', null]],
      apply(d, x, A, p, cal) {
        const L = d.levers, share = clamp(p.share, 0, 1), mix = Object.keys(L.flap_graft.mix);
        // this clinician's cases keep the Mohs and lose the closure: a case with no flap or graft, its first stage paid in full
        const y = Object.assign({}, x, { fg_share: clamp(x.fg_share, 0, 2) * (1 - share) });
        // what the other surgeon bills for those closures (and their add-on codes): in full, nothing to pair with
        const ev = CM.evaluate(d, Object.assign({}, x, { stage: { codes: mix, share: 1 } }), cal);
        let moved = 0;
        for (const c of [...mix, ...Object.keys(L.flap_graft.add_ons || {})]) moved += ((ev.byCode[c] || {}).collections || 0) * share;
        const kept = p.employed ? moved : 0;
        // an employed surgeon's closures still use the practice's supplies
        const U = CM.units(d, x), perDay = mix.reduce((t, c) => t + (U[c] || 0), 0) * share;
        const perClosure = num(A.costs.per_closure), days = x.days_per_week * x.weeks_per_year, pay = num(p.cost);
        const needs = pay == null ? ['param.cost'] : [];
        return { x: y, extra: { collections: kept, practice_only: kept,
                                cost: (pay || 0) * 100 + (p.employed && perClosure != null ? perDay * perClosure * days * 100 : 0) },
                 notes: [`${perDay.toFixed(2)} closures a clinic day, about ${Math.round(moved / 100).toLocaleString()} dollars a year, move to the other surgeon${p.employed ? ', kept by the practice' : ''}. This clinician's first stages on those cases are paid in full.`], needs };
      },
    },
    {
      id: 'biopsy_return', title: 'Bring biopsies back on a separate day (2027)', tier: 'high', only: 'next', whatif: true,
      why: "From 2027, a Medicare visit billed with a biopsy the same day pays 20% less on every service but the highest. A biopsy done at a later procedure-only visit avoids the cut and uses another slot. A what-if: it is shown apart and never added to the plan's total.",
      compliance: "CMS, in the 2027 final rule: bringing patients back another day \"would actually increase beneficiary cost-sharing\"; it does \"not find it appropriate to schedule medical services for patients to maximize payment\"; telling patients that Medicare no longer pays for same-day procedures misleads them; and it will look for the pattern with claims edits, comparative billing reports and medical review. The first day's visit must be a separately billable visit on its own: the decision to do a minor procedure is part of the procedure (NCCI policy manual, ch. I). No copay waivers to ease the second trip. No visit billed on the return day without a separate problem. A delayed biopsy delays the diagnosis, and some patients will not come back.",
      params: [['share', 'Share of biopsied Medicare patients asked to come back', 0, 1, 0.05, 'pct', 0],
               ['return_rate', 'Of those, the share who come back', 0, 1, 0.01, 'pct', 0.9],
               ['full', 'The schedule is full: each return visit takes a slot another patient would have had', 0, 1, 1, 'bool', 1]],
      apply(d, x, A, p, cal) {
        // the biopsied patients whose plan takes Medicare's 2027 rules: Medicare, and the Medicare Advantage plans the
        // Calculator has following it
        const follows = g => CM.followsMedicare ? CM.followsMedicare(d, x, g) : CM.payerKind(d, g) === 'medicare';
        const med = (d.mix.biopsy || d.mix._overall || []).reduce((t, [g, s]) => t + (follows(g) ? s : 0), 0);
        const bx = Math.max(0, x.bx_patients), vs0 = clamp(x.bx_visit_share, 0, 1), share = clamp(p.share, 0, 1);
        const moved = bx * med * vs0 * share;
        const lost = moved * (1 - clamp(p.return_rate, 0, 1));
        const back = moved - lost;
        // the rule's input: the biopsied Medicare patients with a visit the same day lose the moved ones, once; those who
        // never return lose the biopsy too, so they leave the biopsied patients and the share is taken of what is left
        const bx2 = Math.max(0, bx - lost);
        const vs = bx2 > 0 ? clamp(bx * vs0 * (1 - share) / bx2, 0, 1) : 0;
        const y = Object.assign({}, x, { bx_visit_share: vs, bx_patients: bx2 });
        const turn = num(A.minutes.turnover), perEnc = num(A.costs.per_encounter), days = x.days_per_week * x.weeks_per_year;
        const needs = [];
        if (back > 0 && turn == null) needs.push('minutes.turnover');
        if (back > 0 && perEnc == null) needs.push('costs.per_encounter');
        const slot = p.full ? back * days * slotValue(d, x, cal) : 0;
        // what each patient brought back pays more: 20% of the cut avoided on the first day
        let coins = null;
        if (moved > 0) {
          const a = CM.evaluate(d, x, cal).sameDay, b = CM.evaluate(d, Object.assign({}, x, { bx_visit_share: vs0 * (1 - share) }), cal).sameDay;
          if (a && b) coins = COINSURANCE * (a.allowed - b.allowed) / (moved * days);
        }
        return { x: y, extra: { minutes: back * (turn || 0), encounters: back, cost: back * (perEnc || 0) * days * 100, collections: -slot },
                 notes: [`${back.toFixed(2)} extra procedure-only visits a clinic day; ${lost.toFixed(2)} biopsies a day not done because the patient did not return${slot ? `; the slots the return visits take would have brought about ${Math.round(slot / 100).toLocaleString()} dollars a year` : ''}.`
                   + (moved > 0 ? ` Each patient brought back pays${coins > 0 ? ` about ${Math.round(coins / 100).toLocaleString()} dollars` : ''} more in coinsurance, makes a second trip, and waits longer for the diagnosis.` : '')
                   + ` Not counted: the multiple-procedure reduction also avoided on other procedures of the visit day (about $16 a moved patient on the archive), and a second copayment for many patients. ${x.ma_same_day === false ? 'Medicare Advantage patients are left out: the Calculator has their plans not following Medicare\'s 2027 rules.' : 'Medicare Advantage patients are counted where the Calculator has their plan following Medicare\'s 2027 rules.'}`], needs };
      },
    },
    {
      id: 'asc', title: 'Close Mohs wounds, and do excisions, in the ASC', tier: 'medium',
      applies: d => d.tier !== 'nonphysician' && ascHere(d, ['mohs', 'excision']),
      why: "Mohs stays in the clinic, billed there at the office amount. What can move to the ASC at Newburgh is the closure after a clinic Mohs, the same day, and a physician's excision with its repair. In the ASC the surgeon is paid Medicare's lower facility amount, and a same-day closure is still cut 50% against the clinic Mohs; the ASC is paid a facility fee, which the practice keeps only in proportion to its share of the ASC. The plan starts from where each closure is done now and counts only the change set here.",
      compliance: "Choose the site for each patient: Medicare leaves the setting to the physician's clinical judgment for that patient (Claims Processing Manual ch. 14 sec. 20.1). A routing that follows the office rather than the patient is an exposure: the same surgeons close these wounds in the clinic in Kentucky. Mohs is never billed on the ASC claim. Only a physician may operate in an ASC (42 CFR 416.42), so an APP's excision cannot move as the APP's case. The ASC must be a distinct entity: where it shares space with the clinic, the clinic must be closed while the ASC is open, so a time-shared procedure room supports only later-day cases. It needs Medicare certification and an Indiana license, and must tell patients in writing who owns it. For a physician-owned ASC, the anti-kickback safe harbor (42 CFR 1001.952(r)) is voluntary; it asks for returns in proportion to capital invested and operating space dedicated to the ASC. The facility fee is the practice's revenue, never a surgeon's own production. A visit billed with modifier 57 on the ASC claim on a Mohs and closure day is mostly denied by WPS (CO-97, M144) and is not counted here as revenue: have coding review it. Commercial and Medicare Advantage plans may need the site approved first.",
      params: [['to_asc', 'Of the same-day closures now done in the Newburgh clinic, the share moved to the ASC', 0, 1, 0.05, 'pct', 0],
               ['to_clinic', 'Of the same-day closures now done in the ASC, the share brought back to the clinic', 0, 1, 0.05, 'pct', 0],
               ['flap_graft', 'Move flaps, grafts and other closures', 0, 1, 1, 'bool', 1],
               ['layered', 'Move intermediate and complex repairs', 0, 1, 1, 'bool', 1],
               ['excisions', "Of a physician's excisions with a repair now done in the Newburgh clinic, the share done entirely in the ASC", 0, 1, 0.05, 'pct', 0],
               ['own', "Share of the ASC's facility fee the practice keeps (by capital)", 0, 1, 0.01, 'pct', null],
               ['commercial_pct', "Facility fees of Medicare Advantage and commercial plans at the ASC, as a share of Medicare's ASC rates", 0, 3, 0.05, 'pct', null],
               ['ma_cross', 'Medicare Advantage and commercial plans cut the second procedure across the clinic and ASC claims, as Medicare does', 0, 1, 1, 'bool', 1],
               ['ascqr', 'The ASC does not meet quality reporting (ASCQR): about 2% less on A2, G2 and P2 codes', 0, 1, 1, 'bool', 0],
               ['em_rule', "Open question: the 2027 same-day visit rule (20% off all but the highest service) also cuts the ASC claim's lines", 0, 1, 1, 'bool', 0]],
      apply(d, x, A, p) {
        const plan = ascPlan(p), y = Object.assign({}, x, { asc_plan: plan });
        const T = ascTotals(d, x, plan), Bk = T.book;
        if (!Bk.ok) return { x: y, extra: {}, notes: [`No ASC rates or counted cases for ${Bk.Y}.`], needs: [] };
        const own = plan.own, ownV = own == null ? 0 : own;
        const dollars = v => `${v < 0 ? '-' : ''}$${Math.round(Math.abs(v) / 100).toLocaleString()}`;
        const signed = v => (v > 0 ? '+' : '') + dollars(v);
        const asc = num(A.costs.asc_per_case), saved = num(A.costs.office_per_case_saved);
        const net = T.moved + T.exc - T.back;            // cases a year into the ASC, less those brought back
        const needs = [];
        if (own == null) needs.push('param.own');
        const other = T.pool.n - T.pool.medicare;
        if (plan.commercial_pct == null && Bk.rows.some(r => r.perYear != null && r.kind !== 'medicare' && ASC_KINDS.includes(r.kind))) needs.push('param.commercial_pct');
        const costMissing = asc == null && (own == null || own > 0) && Math.abs(net) > 1e-9;
        if (costMissing) needs.push('costs.asc_per_case');
        const cost = net * (ownV * (asc || 0) - (saved || 0)) * 100;
        const notes = [];
        const pct = v => `${Math.round(v * 100)}%`;
        notes.push(T.pool.n > 0
          ? `At Newburgh, ${Math.round(T.pool.n)} same-day Mohs closures a year on this plan: ${Math.round(T.pool.asc)} in the ASC now (${pct(T.pool.asc / T.pool.n)}), ${Math.round(T.pool.clinic)} in the clinic.`
            + (T.moved || T.back ? ` This plan moves ${Math.round(T.moved)} a year to the ASC${T.back ? ` and brings ${Math.round(T.back)} back to the clinic` : ''}.` : '')
            + (T.exc ? ` ${Math.round(T.exc)} excisions a year with their repair go to the ASC.` : '')
          : 'No same-day Mohs closures at the Newburgh office in this window: the ASC is at Newburgh.');
        if (plan.commercial_pct == null && other > 0.5)
          notes.push(`Priced: Medicare fee-for-service cases only, ${pct(T.pool.medicare / T.pool.n)} of these closures. Enter the facility fee of Medicare Advantage and commercial plans, as a share of Medicare's, to price the other ${pct(other / T.pool.n)}.`);
        if (T.moved || T.back || T.exc) {
          notes.push(own == null
            ? `The share of the facility fee the practice keeps is not entered, so the year above counts none of it. With the whole fee: ${signed(T.dPro + T.dFee)} a year; without it: ${signed(T.dPro)}.`
            : own === 0 ? `The practice keeps none of the ASC's fee: only the surgeon's own professional fees change, by ${signed(T.dPro)} a year.`
            : `Facility fees kept by the practice (${pct(own)} of the ASC's): ${signed(ownV * T.dFee)} a year, the practice's revenue, not the surgeon's production. The surgeon's own professional fees change by ${signed(T.dPro)}.`);
          notes.push(costMissing ? "Gross only: the ASC's cost per case is not entered (CMS publishes no ASC cost data), so nothing is taken off for it." : '');
          if (T.coinsN > 0) notes.push(`A Medicare patient pays about ${dollars(T.coins / T.coinsN)} more per case moved to the ASC (20% of each allowed amount; the ASC's coinsurance is not capped).`);
          else if (T.coinsBack > 0) notes.push('A Medicare patient brought back to the clinic pays less coinsurance: there is no ASC bill.');
        }
        if (T.current.n > 0)
          notes.push(`The routing now: the ${Math.round(T.current.n)} priced closures a year already in the ASC, against closing them in the clinic, are worth ${own == null ? `${signed(T.current.pro + T.current.fee)} a year with the whole facility fee, ${signed(T.current.pro)} without it` : `${signed(T.current.pro + ownV * T.current.fee)} a year with ${pct(own)} of the fee (${signed(T.current.pro)} without it)`}. Already in the plan's history, not added to it.`);
        if (T.split > 0.5) notes.push(`${Math.round(T.split)} cases a year have the excision or part of the closure in the clinic and the rest in the ASC; they are left as they are.`);
        const types = ASC_TYPES.map(([k, label]) => {
          const t = T.types[k];
          if (!t || !(t.med > 0)) return null;
          const per = v => v / t.med;
          return { type: k, label, perYear: t.n, asc: t.asc, clinic: t.clinic, A: per(t.A), B: per(t.B), C: per(t.C), fee: per(t.fee),
                   owner: per(t.B + t.fee - t.A), nonOwner: per(t.B - t.A), coins: COINSURANCE * per(t.B + t.fee - t.A), delay: per(t.C - t.B),
                   lowMargin: ASC_LOW_MARGIN.includes(k) };
        }).filter(Boolean);
        return { x: y, extra: { collections: T.dPro + ownV * T.dFee, practice_only: ownV * T.dFee, cost },
                 notes: notes.filter(Boolean), needs,
                 detail: { year: Bk.Y, own, withFee: T.dPro + T.dFee, withoutFee: T.dPro, fees: T.dFee, pro: T.dPro, moved: T.moved, back: T.back,
                           excisions: T.exc, current: T.current, pool: T.pool, types, from: Bk.from, costKnown: asc != null } };
      },
    },
    {
      id: 'asc_later', title: 'Close in the ASC on a later day, not the Mohs day', tier: 'high', whatif: true,
      applies: d => d.tier !== 'nonphysician' && ascHere(d, ['mohs']),
      why: "A closure done on another day than the Mohs is not cut 50% against it, and Mohs has a 0-day global, so the later closure needs no modifier. All of the gain comes from the delay: the ASC's fee is the same either day. A what-if: it is shown apart and never added to the plan's total.",
      compliance: "Stage a closure only for a clinical reason that exists at the time, written in the note when the decision is made. CMS, in the 2027 final rule: \"we find the possible practice of scheduling medical services to maximize payment highly problematic\" and \"we do not find it appropriate to schedule medical services for patients to maximize payment\"; it will look for the pattern with claims edits, comparative billing reports and medical review. NCCI policy manual, ch. I: \"Physicians shall not inconvenience beneficiaries nor increase risks to beneficiaries by performing services on different dates of service to avoid MUE or NCCI PTP edits.\" The patient makes a second trip with an open wound and pays more coinsurance. Use modifier 58 or 79 only when the later day falls inside another global period of the same surgeon or group.",
      params: [['share', 'Of the same-day closures in the ASC on this plan, the share done on a later day instead', 0, 1, 0.01, 'pct', 0]],
      apply(d, x, A, p) {
        const plan = x.asc_plan || ascPlan(paramDefaults(STRATEGIES.find(S => S.id === 'asc')));
        const Bk = ascBook(d, x, plan), share = clamp(p.share, 0, 1);
        if (!Bk.ok) return { x, extra: {}, notes: [`No ASC rates or counted cases for ${Bk.Y}.`], needs: [] };
        let n = 0, gain = 0, coins = 0, cn = 0;
        for (const r of Bk.rows) {
          if (r.what !== 'mohs' || !r.priced) continue;
          const moved = ascInScope(plan, r.type) ? (r.site === 'clinic' ? plan.to_asc : r.site === 'asc' ? 1 - plan.to_clinic : 0) : (r.site === 'asc' ? 1 : 0);
          if (r.site === 'clinic' && !(r.kind === 'medicare' || plan.commercial_pct != null)) continue;
          const k = r.perYear * moved * share;
          if (!(k > 0)) continue;
          n += k; gain += k * (r.v.C - r.v.B) * r.coll;
          if (r.medicare) { coins += k * COINSURANCE * (r.v.C - r.v.B); cn += k; }
        }
        const dollars = v => `$${Math.round(Math.abs(v) / 100).toLocaleString()}`;
        return { x, extra: { collections: gain },
                 notes: [`${Math.round(n)} closures a year done in the ASC on a later day instead of the Mohs day, about ${dollars(gain)} a year more in the surgeon's professional fees: the 50% same-day cut no longer applies. The ASC's fee does not change.`
                   + (cn > 0 ? ` A Medicare patient pays about ${dollars(coins / cn)} more in coinsurance, and makes a second trip with an open wound.` : '')], needs: [] };
      },
    },
    {
      id: 'app_shift', title: 'Hand routine follow-up visits to an APP', tier: 'low', applies: d => d.tier === 'physician',
      why: "Each established visit the physician hands to an APP frees physician minutes for work only the physician can do, such as Mohs. The APP's visit is paid at the APP's rate (Medicare: 85%).",
      compliance: "Billed under the APP's own number it is ordinary practice. Billed under the physician (incident to) it needs an established problem, a plan of care the physician started, and the physician in the office suite (or, for services without a 010 or 090 global, present by real-time audio and video). A new problem raised at the visit cannot be billed incident to.",
      params: [['visits', 'Established visits a day handed to an APP', 0, 20, 0.5, 'n1', 3],
               ['fill', 'Use the freed physician minutes for more Mohs cases (only where referrals are waiting)', 0, 1, 1, 'bool', 0],
               ['app_rate', "The APP's visit paid, against the physician's", 0.5, 1, 0.01, 'pct', 0.85]],
      apply(d, x, A, p, cal) {
        const n = Math.min(Math.max(0, p.visits), Math.max(0, x.visits) * (1 - clamp(x.new_share, 0, 1)));
        const y = Object.assign({}, x, { visits: x.visits - n, new_share: x.visits - n > 0 ? x.visits * x.new_share / (x.visits - n) : 0 });
        const needs = [];
        const m = A.minutes, perVisit = num(m.visit_est);
        if (perVisit == null) needs.push('minutes.visit_est');
        let why = '';
        if (p.fill && n > 0) {
          if (!(x.mohs_cases > 0)) why = ' This physician does no Mohs, so the freed minutes are left free.';
          else {
            const perCase = num(m.mohs_case), stage = num(m.mohs_stage) || 0, cl = num(m.flap_graft), ly = num(m.layered);
            if (perCase == null) needs.push('minutes.mohs_case');
            if (perVisit != null && perCase != null) {
              const caseMin = perCase + Math.max(0, x.mohs_stages - 1) * stage + clamp(x.fg_share, 0, 2) * (cl || 0) + clamp(x.lay_mohs, 0, 2) * (ly || 0);
              if (caseMin > 0) y.mohs_cases = x.mohs_cases + n * perVisit / caseMin;
              else why = " A Mohs case's minutes are 0 in Practice figures, so the freed time is not turned into cases.";
            }
          }
        }
        // the APP's side: the same established visits, valued at this physician's established codes, at the APP's rate
        const before = CM.evaluate(d, x, cal), days = x.days_per_week * x.weeks_per_year;
        const est = ['99211', '99212', '99213', '99214', '99215'].reduce((t, c) => t + ((before.byCode[c] || {}).collections || 0), 0);
        const estYear = Math.max(0, x.visits) * (1 - clamp(x.new_share, 0, 1)) * days;
        const perVisitColl = estYear > 0 ? est / estYear : 0;
        const appColl = n * days * perVisitColl * clamp(p.app_rate, 0, 1);
        const appCost = num(A.costs.app_per_visit);
        if (appCost == null) needs.push('costs.app_per_visit');
        return { x: y, extra: { collections: appColl, practice_only: appColl, cost: n * days * (appCost || 0) * 100, minutes: 0 },
                 notes: [`${n.toFixed(1)} visits a day move to an APP${y.mohs_cases !== x.mohs_cases ? `; the physician takes ${(y.mohs_cases - x.mohs_cases).toFixed(2)} more Mohs cases a day in the freed time` : (perVisit != null && n > 0 ? `, freeing about ${Math.round(n * perVisit)} physician minutes a day` : '')}. The APP's visits are counted as the practice's, not this clinician's.${why}`],
                 needs };
      },
    },
    {
      id: 'b1', title: 'Bill the visit complexity add-on wherever it applies', tier: 'medium',
      why: "G2211 (from 2027, modifier B1 at 16% of the visit) pays Medicare visits that are part of ongoing care for a serious or complex condition. It is not paid on a visit billed with modifier 25.",
      compliance: "Only where the note shows the ongoing relationship for a serious or complex condition; CMS's own example of a visit that does not qualify is a mole removal. Not with modifier 25, except preventive services, a wellness visit or a vaccine. Appended to every visit by default it invites review. Medicare only: commercial payers are not expected to pay it.",
      params: [['capture', 'Share of Medicare visits without a same-day procedure billed with it', 0, 1, 0.05, 'pct', 0.5],
               ['eligible', 'Share of Medicare visits with no same-day procedure', 0, 1, 0.05, 'pct', 0.6]],
      apply(d, x, A, p) {
        const med = kindShare(d, 'est_visit', ['medicare']);
        const per = med * clamp(p.eligible, 0, 1) * clamp(p.capture, 0, 1);
        const cur = (d.levers.visits.g2211_per_visit || 0);
        if (per <= cur) return { x, extra: {}, notes: [`Already billed on ${(cur * 100).toFixed(1)} of every 100 visits, at or above this target: check that the notes support that rate.`], needs: [] };
        return { x: Object.assign({}, x, { g2211_scale: cur > 0 ? per / cur : 0 }), extra: {},
                 notes: [cur > 0 ? `From ${(cur * 100).toFixed(1)} to ${(per * 100).toFixed(1)} add-ons per 100 visits.` : 'This clinician has not billed it: the page cannot price it from history.'], needs: [] };
      },
    },
    {
      id: 'add_session', title: 'Add clinic time', tier: 'low',
      why: 'More clinic days carry the day\'s fixed costs with them.',
      compliance: '',
      params: [['days', 'Clinic days a week added', 0, 3, 0.5, 'n1', 0.5]],
      apply(d, x, A, p) {
        const add = Math.max(0, p.days), fixed = num(A.costs.per_clinic_day);
        return { x: Object.assign({}, x, { days_per_week: x.days_per_week + add }), extra: {},
                 notes: [], needs: fixed == null ? ['costs.per_clinic_day'] : [] };
      },
    },
    {
      id: 'collect', title: 'Collect patient balances at the visit', tier: 'low',
      why: 'Balances collected at check-out are collected far more often than balances billed later.',
      compliance: 'Estimate from the payer\'s eligibility response. Never collect Medicare cost-sharing from a qualified Medicare beneficiary (QMB), even when Medicaid pays nothing. Check Medicare Advantage and commercial contracts for limits on collecting before the claim is decided. Refund credit balances promptly.',
      params: [['points', 'Points more of patient balances collected', 0, 0.5, 0.01, 'pct', 0.1], ['cost', 'What it costs a year (staff, terminals), dollars', 0, 200000, 1000, 'usd', 0]],
      apply(d, x, A, p) {
        const q = Object.fromEntries(Object.entries(x.q || CM.Q_DEFAULT).map(([k, v]) => [k, k === 'medicaid' ? 0 : Math.min(1, v + clamp(p.points, 0, 1))]));
        return { x: Object.assign({}, x, { q }), extra: { cost: Math.max(0, +p.cost || 0) * 100 }, notes: [], needs: [] };
      },
    },
    {
      id: 'appeals', title: 'Work the open appeals', tier: 'low',
      why: 'RemitWatch\'s findings for this clinician that can still be appealed: denials, bundling, downcoding and underpayments.',
      compliance: '',
      params: [['share', 'Share of the open, still-appealable dollars recovered', 0, 1, 0.05, 'pct', 0.4]],
      apply(d, x, A, p) {
        const St = d.strategy || {}, open = St.open_findings || {};
        const cents = Object.values(open).reduce((t, v) => t + (v.cents || 0), 0), n = Object.values(open).reduce((t, v) => t + (v.n || 0), 0);
        // for a role, the payload gives each category per clinician on average (the group's totals apart)
        const avg = St.open_findings_clinicians != null;
        return { x, extra: { one_time: cents * clamp(p.share, 0, 1) },
                 notes: [`${Math.round(n).toLocaleString()} open findings, ${Math.round(cents / 100).toLocaleString()} dollars${avg ? ` per clinician on average (${St.open_findings_clinicians} clinicians)` : ''}.`], needs: [] };
      },
    },
    {
      id: 'vendor_fees', title: 'Get paid without payment-vendor fees', tier: 'low',
      why: 'Fees taken from payments by card and payment vendors (practice-wide, from the remittances). HIPAA-covered health plans must pay by standard ACH EFT on request (after enrollment) and cannot require virtual cards; fee-bearing vendor tiers can be refused. Workers\' comp and auto carriers are outside HIPAA.',
      compliance: '',
      params: [['share', 'Share of the fees stopped', 0, 1, 0.05, 'pct', 0.8]],
      apply(d, x, A, p) {
        const St = d.strategy || {}, fees = Object.values(St.vendor_fees || {}).reduce((t, v) => t + v, 0);
        // summed over the window's months (vendor_fees_months, from the payload), made a year
        const months = Math.max(1, St.vendor_fees_months > 0 ? St.vendor_fees_months : monthsBetween(d.from, d.to));
        const year = fees * 12 / months * clamp(p.share, 0, 1);
        return { x, extra: { collections: year, practice_only: year }, notes: ['Practice-wide, not this clinician\'s.'], needs: [] };
      },
    },
    {
      id: 'contract_month', title: 'Contract corrections take effect partway through the year', tier: 'low',
      why: 'A renegotiated contract usually starts on a date months after the first ask. Only the months after it count.',
      compliance: '',
      params: [['month', 'Month the corrected contracts start (1 = January)', 1, 12, 1, 'n0', 7]],
      apply(d, x, A, p, cal, ctx) {
        if (!Object.values(x.corrected || {}).some(Boolean)) return { x, extra: {}, notes: ['No contract is ticked for correction on the Calculator view, so there is nothing to start partway.'], needs: [] };
        const months = 13 - clamp(Math.round(p.month), 1, 12), off = 1 - months / 12;
        // the corrections' worth in the year: the plan's, and what the strategies above add at the corrected rates
        const full = CM.evaluate(d, x, cal), none = CM.evaluate(d, Object.assign({}, x, { corrected: {} }), cal);
        const ef = ctx ? ctx.extras() : {}, en = ctx ? ctx.extrasWith({ corrected: {} }) : {};
        const z = v => +v || 0;
        const total = (full.totalCollections - none.totalCollections) + z(ef.collections) - z(en.collections);
        const own = (full.collections - none.collections) + (z(ef.collections) - z(ef.practice_only)) - (z(en.collections) - z(en.practice_only));
        return { x, extra: { collections: -total * off, practice_only: -(total - own) * off },
                 notes: [`${months} month${months === 1 ? '' : 's'} of the corrected contracts in the year.`], needs: [] };
      },
    },
  ];

  function monthsBetween(a, b) {
    if (!a || !b) return 12;
    return (+b.slice(0, 4) - +a.slice(0, 4)) * 12 + (+b.slice(5, 7) - +a.slice(5, 7)) + 1;
  }

  function paramDefaults(S) { return Object.fromEntries(S.params.map(p => [p[0], p[6]])); }

  // ------------------------------------------------------------------ run a scenario
  // Fields of the plan a strategy may read that a later one may change. When a later strategy changes one (more clinic
  // days, more of patient balances collected, more Mohs cases), each earlier strategy's own extras (visits, costs, the
  // APP's visits...) are worked out again on it, so the year does not depend on the order the strategies stack in.
  const SHARED = ['days_per_week', 'weeks_per_year', 'q', 'mohs_cases'];
  const ZERO = { collections: 0, cost: 0, minutes: 0, encounters: 0, one_time: 0, practice_only: 0, wrvu: 0 };

  // chosen: {strategyId: {on: bool, params: {...}}}. Returns the plan before, each strategy's step, and the result. The
  // what-ifs (strategies marked whatif) come last, on top of the result, and never change it: result is the plan's
  // headline, whatif (or null) the year with them.
  function run(d, x, A, chosen, cal) {
    A = filled(A);
    cal = cal || CM.calibrate(d);
    const next = d.next_year && (x.year === d.next_year);
    const add = (t, e) => { for (const k of Object.keys(ZERO)) t[k] += +((e || {})[k]) || 0; return t; };
    const sumUp = (y, e) => {
      const ev = CM.evaluate(d, y, cal);
      const act = activity(d, y), days = ev.daysYear;
      const min = minutes(act, A), cost = costs(act, A, days);
      return { ev, act, minutes: min, costs: cost, wrvu: wrvus(d, y) + e.wrvu,
               // what the practice collects: the plan (with the ancillary lines when the calculator adds them) and the extras
               collections: ev.totalCollections + e.collections,
               // the clinician's own production, which is all a pay plan may count: never the ancillary lines or the
               // pathology their biopsies bring, nor the practice's extras (APP visits, vendor fees, another surgeon)
               ownProduction: ev.collections + e.collections - e.practice_only,
               practiceOnly: (ev.totalCollections - ev.collections) + e.practice_only,
               cost: cost.total + e.cost, minutesDay: min.total + e.minutes, encountersDay: act.encounters + e.encounters, oneTime: e.one_time };
    };
    const base = sumUp(x, Object.assign({}, ZERO));
    const done = [];            // the strategies applied: {S, params, xin, keep, key, extra}
    const keyOf = (y, keys) => JSON.stringify(keys.map(k => y[k]));
    let cur = x, prev = base;
    // an applied strategy again, on its own starting plan with the shared fields as they stand in y
    const redo = (i, y, over) => {
      const rec = done[i], xin = Object.assign({}, rec.xin);
      for (const k of rec.keep) xin[k] = y[k];
      Object.assign(xin, over || {});
      return ((rec.S.apply(d, xin, A, rec.params, cal, ctxFor(i, y)) || {}).extra) || {};
    };
    // what a strategy may ask of the ones before it: their extras now, or worked out again with some of the plan changed
    const ctxFor = (i, y) => ({
      extras: () => done.slice(0, i).reduce((t, rec) => add(t, rec.extra), Object.assign({}, ZERO)),
      extrasWith: over => done.slice(0, i).reduce((t, rec, j) => add(t, redo(j, y, over)), Object.assign({}, ZERO)),
    });
    const steps = [];
    let result = null, headlineX = x;
    const order = [...STRATEGIES.filter(S => !S.whatif), ...STRATEGIES.filter(S => S.whatif)];
    for (const S of order) {
      if (S.whatif && !result) { result = prev; headlineX = cur; }
      const c = chosen && chosen[S.id];
      if (!c || !c.on) continue;
      if (S.only === 'next' && !next) { steps.push({ id: S.id, title: S.title, whatif: !!S.whatif, skipped: 'Medicare\'s 2027 rules are not on: switch the calculator to Plan 2027.' }); continue; }
      if (S.applies && !S.applies(d, cur)) { steps.push({ id: S.id, title: S.title, whatif: !!S.whatif, skipped: 'Does not apply to this clinician\'s work.' }); continue; }
      const params = Object.assign(paramDefaults(S), c.params || {});
      const r = S.apply(d, cur, A, params, cal, ctxFor(done.length, cur)) || { x: cur, extra: {} };
      const y = r.x || cur;
      const keep = SHARED.filter(k => y[k] === cur[k]);      // what it changes itself is not taken from later plans
      done.push({ S, params, xin: cur, keep, key: keyOf(y, keep), extra: r.extra || {} });
      cur = y;
      for (let i = 0; i < done.length - 1; i++) {
        const k = keyOf(cur, done[i].keep);
        if (k !== done[i].key) { done[i].extra = redo(i, cur); done[i].key = k; }
      }
      const e = done.reduce((t, rec) => add(t, rec.extra), Object.assign({}, ZERO));
      const now = sumUp(cur, e);
      const own = r.extra || {};
      steps.push({ id: S.id, title: S.title, tier: S.tier, whatif: !!S.whatif, notes: r.notes || [], needs: r.needs || [],
                   collections: now.collections - prev.collections, ownProduction: now.ownProduction - prev.ownProduction,
                   cost: now.cost - prev.cost, minutesDay: now.minutesDay - prev.minutesDay,
                   encountersDay: now.encountersDay - prev.encountersDay, oneTime: +own.one_time || 0, practiceOnly: +own.practice_only || 0,
                   wrvu: now.wrvu - prev.wrvu, per_case: r.per_case, detail: r.detail || null });
      prev = now;
    }
    if (!result) { result = prev; headlineX = cur; }
    const whatif = prev !== result ? prev : null;
    const session = num(A.session_minutes);
    return { base, steps, result, whatif, x: headlineX, whatifX: whatif ? cur : null,
             capacity: session ? { available: session, used: result.minutesDay, share: result.minutesDay / session } : null,
             perMinute: minutesValue(d, headlineX, A, cal), daysYear: result.ev.daysYear };
  }

  // collections per clinician minute for each kind of work, from the plan's own prices (what earns the most of the day)
  function minutesValue(d, x, A, cal) {
    const ev = CM.evaluate(d, x, cal), act = activity(d, x), m = A.minutes, days = ev.daysYear || 1;
    const per = (line, n) => n > 0 ? ((ev.byLine[line] || {}).collections || 0) / (n * days) : null;
    const rows = [
      ['Mohs case (first stage and extra stages)', per('mohs', act.mohs), num(m.mohs_case) == null ? null : num(m.mohs_case) + Math.max(0, x.mohs_stages - 1) * (num(m.mohs_stage) || 0)],
      ['Closure (flap, graft or layered)', per('closures', act.fg + act.lay), (act.fg + act.lay) > 0 && num(m.flap_graft) != null && num(m.layered) != null ? (act.fg * num(m.flap_graft) + act.lay * num(m.layered)) / (act.fg + act.lay) : null],
      ['Excision', per('excisions', act.exc), num(m.excision)],
      ['Visit', per('visits', act.vn + act.ve), (act.vn + act.ve) > 0 && num(m.visit_new) != null && num(m.visit_est) != null ? (act.vn * num(m.visit_new) + act.ve * num(m.visit_est)) / (act.vn + act.ve) : null],
      ['Biopsy', per('biopsies', act.bx), num(m.biopsy)],
      ['AK patient', per('ak', act.ak), num(m.ak_patient)],
      ['Benign-destruction patient', per('benign', act.ben), num(m.benign_patient)],
    ];
    return rows.map(([label, value, mins]) => ({ label, value, minutes: mins, perMinute: value != null && mins ? value / mins : null }));
  }

  const api = { MINUTES, COSTS, COMP_TYPES, STRATEGIES, MAX_SCENARIOS, blankAssumptions, clean, filled, activity, minutes, costs, wrvus, pay,
                collectionsFor, planMissing, run, minutesValue, paramDefaults, BIGGER,
                ascCase, ascFee, dayPro, ascBook, ascTotals, ascPlan, caseLabel, closureType, ASC_TYPES };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.LabModel = api;
})(typeof window !== 'undefined' ? window : globalThis);
