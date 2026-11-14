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
    ['asc_per_case', 'A case in the ASC, all in'],
    ['office_per_case_saved', 'What the office saves when a case goes to the ASC'],
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
  const fig = v => (typeof v === 'number' && isFinite(v) && v >= 0) ? v : null;
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

  // one Mohs case at one payer, in the office or in an ASC: the professional fee (and the ASC's facility fee), with the
  // multiple-procedure rules of each, before collections. cases[] = [[probability, closure code or null]]
  function caseValue(d, x, cal, g, f, closure, year, site, office) {
    const k = c => (cal.k[c] != null ? cal.k[c] : 1);
    const rho = d.constants.mppr_share;
    const M = c => ((d.medicare[c] || {})[year]), MF = c => ((d.medicare_fac || {})[c] || {})[year];
    const pro = c => {         // the payer's rate in that setting: its office rate times Medicare's facility-to-office ratio
      const p = CM.price(d, x, c, g, year, true);
      if (p == null) return 0;
      if (site !== 'asc') return p * k(c);
      const m = M(c), mf = MF(c);
      return (m && mf ? p * mf / m : p) * k(c);
    };
    const pf = pro(f), extra = Math.max(0, (x.mohs_stages || 1) - 1);       // the plan's stages per case
    const addc = f === '17311' ? '17312' : '17314';
    let v = extra * pro(addc);
    if (closure) {
      const pc = pro(closure);
      v += Math.max(pf, pc) + rho * Math.min(pf, pc);
    } else v += pf;
    let fac = 0;
    if (site === 'asc') {
      const A = (d.asc || {})[year] || {}, amt = c => { const r = A[c]; return r && r[2] ? r[2][office] : null; };
      const lines = [f, closure].filter(Boolean).map(c => [c, amt(c) || 0, A[c] ? A[c][1] : false]).sort((a, b) => b[1] - a[1]);
      lines.forEach(([, a, mpd], i) => { fac += i === 0 || !mpd ? a : a * 0.5; });      // extra stages are packaged (N1)
    }
    return { pro: v, facility: fac };
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
        const med = kindShare(d, 'biopsy', x.ma_same_day ? ['medicare', 'medicare_advantage'] : ['medicare']);
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
                   + ` Not counted: the multiple-procedure reduction also avoided on other procedures of the visit day (about $16 a moved patient on the archive), and a second copayment for many patients.${x.ma_same_day ? '' : ' Medicare Advantage patients gain nothing unless their plan applies the rule.'}`], needs };
      },
    },
    {
      id: 'asc', title: 'Do Mohs cases with a flap or graft in the ASC', tier: 'medium', applies: (d, x) => x.mohs_cases > 0,
      why: "In an ASC the surgeon is paid Medicare's facility amount, lower than the office, and the ASC is paid a facility fee. For a case closed with a flap or graft the facility fee is large (the closure is a full surgical procedure there); for a Mohs case alone or with a layered repair it is small, and the office pays more.",
      compliance: "Choose the site for the patient. A physician-owned ASC needs its ownership to meet the anti-kickback safe harbor for ASCs, and commercial payers may need the site approved first.",
      params: [['cases', 'Mohs cases a week moved to the ASC', 0, 40, 0.5, 'n1', 2],
               ['scope_all', 'Move every kind of Mohs case, not only those closed with a flap or graft', 0, 1, 1, 'bool', 0],
               ['own', "The practice's share of the ASC's facility fee", 0, 1, 0.05, 'pct', 1],
               ['commercial_pct', "Commercial and Medicare Advantage facility fees, as a share of Medicare's", 0.5, 3, 0.05, 'pct', 1]],
      apply(d, x, A, p, cal) {
        const year = x.year || d.rate_year;
        if (!((d.asc || {})[year])) return { x, extra: {}, notes: ['No ASC rates for this year.'], needs: [] };
        const office = 'newburgh';
        const L = d.levers, fg = Object.entries(L.flap_graft.mix), lay = Object.entries(L.layered.mix);
        const R = clamp(x.fg_share, 0, 2), Lm = clamp(x.lay_mohs, 0, 2), T = R + Lm, a = Math.min(1, T);
        const kinds = c => [...(p.scope_all ? [[1 - a, null]] : []), ...fg.map(([c2, s]) => [(T > 0 ? R / T * a : 0) * s, c2]),
                            ...(p.scope_all ? lay.map(([c2, s]) => [(T > 0 ? Lm / T * a : 0) * s, c2]) : [])];
        const shapes = kinds(), wsum = shapes.reduce((t, [w]) => t + w, 0) || 1;
        let delta = 0;
        for (const [f, site] of [['17311', clamp(x.mohs_hn, 0, 1)], ['17313', 1 - clamp(x.mohs_hn, 0, 1)]]) {
          for (const [g, s] of CM.mixOf(d, f)) {
            const kind = CM.payerKind(d, g), feeShare = kind === 'medicare' ? 1 : clamp(p.commercial_pct, 0, 5);
            for (const [w, closure] of shapes) {
              const o = caseValue(d, x, cal, g, f, closure, year, 'office', office);
              const z = caseValue(d, x, cal, g, f, closure, year, 'asc', office);
              const coll = CM.collectShare(d, x, g, f);
              delta += site * s * (w / wsum) * ((z.pro - o.pro) * coll + z.facility * feeShare * clamp(p.own, 0, 1) * coll);
            }
          }
        }
        const casesYear = Math.max(0, p.cases) * x.weeks_per_year;
        const asc = num(A.costs.asc_per_case), saved = num(A.costs.office_per_case_saved);
        const needs = [];
        if (asc == null) needs.push('costs.asc_per_case');
        if (saved == null) needs.push('costs.office_per_case_saved');
        return { x, extra: { collections: delta * casesYear, cost: ((asc || 0) - (saved || 0)) * casesYear * 100 },
                 notes: [`${casesYear.toFixed(0)} cases a year, each about ${Math.round(delta / 100).toLocaleString()} dollars more collected in the ASC than in the office before costs (at ${Math.round(clamp(p.own, 0, 1) * 100)}% of the facility fee).`],
                 needs, per_case: delta };
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
        return { x: y, extra: { collections: appColl, cost: n * days * (appCost || 0) * 100, minutes: 0 },
                 notes: [`${n.toFixed(1)} visits a day move to an APP${y.mohs_cases !== x.mohs_cases ? `; the physician takes ${(y.mohs_cases - x.mohs_cases).toFixed(2)} more Mohs cases a day in the freed time` : (perVisit != null && n > 0 ? `, freeing about ${Math.round(n * perVisit)} physician minutes a day` : '')}. The APP's visits are counted as the practice's, not this clinician's.${why}`],
                 needs, practice_only: appColl };
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
        const open = (d.strategy || {}).open_findings || {};
        const cents = Object.values(open).reduce((t, v) => t + (v.cents || 0), 0);
        return { x, extra: { one_time: cents * clamp(p.share, 0, 1) }, notes: [`${Object.values(open).reduce((t, v) => t + (v.n || 0), 0)} open findings, ${Math.round(cents / 100).toLocaleString()} dollars.`], needs: [] };
      },
    },
    {
      id: 'vendor_fees', title: 'Get paid without payment-vendor fees', tier: 'low',
      why: 'Fees taken from payments by card and payment vendors (practice-wide, from the remittances). HIPAA-covered health plans must pay by standard ACH EFT on request (after enrollment) and cannot require virtual cards; fee-bearing vendor tiers can be refused. Workers\' comp and auto carriers are outside HIPAA.',
      compliance: '',
      params: [['share', 'Share of the fees stopped', 0, 1, 0.05, 'pct', 0.8]],
      apply(d, x, A, p) {
        const fees = Object.values((d.strategy || {}).vendor_fees || {}).reduce((t, v) => t + v, 0);
        const months = Math.max(1, monthsBetween(d.from, d.to));
        const year = fees * 12 / months * clamp(p.share, 0, 1);
        return { x, extra: { collections: year }, notes: ['Practice-wide, not this clinician\'s.'], needs: [], practice_only: year };
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
                   wrvu: now.wrvu - prev.wrvu, per_case: r.per_case });
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
                collectionsFor, planMissing, run, minutesValue, paramDefaults, caseValue, BIGGER };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.LabModel = api;
})(typeof window !== 'undefined' ? window : globalThis);
