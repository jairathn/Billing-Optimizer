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

  function blankAssumptions() {
    return { minutes: Object.fromEntries(MINUTES.map(([k]) => [k, null])), costs: Object.fromEntries(COSTS.map(([k]) => [k, null])),
             session_minutes: null, comp: {}, scenarios: [] };
  }
  function filled(A) {
    const b = blankAssumptions();
    A = A || {};
    return { minutes: Object.assign(b.minutes, A.minutes || {}), costs: Object.assign(b.costs, A.costs || {}),
             session_minutes: A.session_minutes == null ? null : A.session_minutes, comp: A.comp || {}, scenarios: A.scenarios || [] };
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
  function wrvus(d, x) {
    const U = CM.units(d, x), year = x.year || d.rate_year, days = Math.max(0, x.days_per_week) * Math.max(0, x.weeks_per_year);
    const table = (d.work_rvu || {})[year] || (d.work_rvu || {})[d.rate_year] || {};
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

  // the collections a pay target needs, under a plan (inverse of pay)
  function collectionsFor(plan, target) {
    if (!plan || !plan.type) return null;
    if (plan.type === 'pct') return plan.pct > 0 ? target / plan.pct : null;
    if (plan.type === 'base') {
      const above = target - (num(plan.base) || 0) * 100;
      if (above <= 0) return (num(plan.threshold) || 0) * 100;
      return plan.above_pct > 0 ? (num(plan.threshold) || 0) * 100 + above / plan.above_pct : null;
    }
    return null;           // a wRVU plan targets wRVUs, not collections
  }

  // ------------------------------------------------------------------ helpers the strategies share
  const BIGGER = c => ['14041', '14061', '14301', '14302', '14350'].includes(c) || (/^15\d{3}$/.test(c) && (+c <= 15261 || (+c >= 15570 && +c <= 15770)));
  function kindShare(d, family, kinds) {
    const mix = d.mix[family] || d.mix._overall || [];
    return mix.reduce((t, [g, s]) => t + (kinds.includes(CM.payerKind(d, g)) ? s : 0), 0);
  }
  const clamp = (v, a, b) => Math.min(b, Math.max(a, +v || 0));

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
      id: 'stage', title: 'Stage the bigger closures to a later day', tier: 'high', applies: (d, x) => x.mohs_cases > 0 && Object.keys(d.levers.flap_graft.mix).length > 0,
      why: "When a flap or graft is done the same day as the Mohs first stage, Medicare's multiple-procedure rule pays the first stage at half. Done on another day, both are paid in full, at the cost of a second visit.",
      compliance: "CMS, in the 2027 final rule: \"we do not find it appropriate to schedule medical services for patients to maximize payment, which would create undue burden and potential medical risk for beneficiaries.\" Stage a closure when the wound or the patient calls for it, and say why in the note. Staging for the fee is a pattern payers and auditors look for.",
      params: [['share', 'Share of the bigger closures done on a later day', 0, 1, 0.05, 'pct', 0.2],
               ['all', 'Count every flap and graft, not only the bigger ones (grafts, interpolation flaps, flaps over 10 sq cm)', 0, 1, 1, 'bool', 0],
               ['return_rate', 'Patients who come back for the closure', 0, 1, 0.01, 'pct', 1]],
      apply(d, x, A, p) {
        const codes = Object.keys(d.levers.flap_graft.mix).filter(c => p.all ? true : BIGGER(c));
        const share = clamp(p.share, 0, 1) * clamp(p.return_rate, 0, 1);
        const unitsFG = CM.units(d, x);
        const staged = codes.reduce((t, c) => t + (unitsFG[c] || 0), 0) * share;
        const turn = num(A.minutes.turnover), perEnc = num(A.costs.per_encounter);
        const days = x.days_per_week * x.weeks_per_year;
        const needs = [];
        if (turn == null) needs.push('minutes.turnover');
        if (perEnc == null) needs.push('costs.per_encounter');
        return { x: Object.assign({}, x, { stage: { codes, share } }),
                 extra: { minutes: staged * (turn || 0), encounters: staged, cost: staged * (perEnc || 0) * days * 100 },
                 notes: [`${staged.toFixed(2)} closures a clinic day move to another visit (${codes.length} closure codes in scope).`], needs };
      },
    },
    {
      id: 'biopsy_return', title: 'Bring biopsies back on a separate day (2027)', tier: 'high', only: 'next',
      why: "From 2027, a Medicare visit billed with a biopsy the same day pays 20% less on every service but the highest. A biopsy done at a later procedure-only visit avoids the cut and uses another slot.",
      compliance: "The same 2027 rule says CMS does \"not find it appropriate to schedule medical services for patients to maximize payment\". A delayed biopsy also delays a diagnosis, and some patients will not come back.",
      params: [['share', 'Share of biopsied Medicare patients asked to come back', 0, 1, 0.05, 'pct', 0.5],
               ['return_rate', 'Of those, the share who come back', 0, 1, 0.01, 'pct', 0.9]],
      apply(d, x, A, p) {
        const med = kindShare(d, 'biopsy', x.ma_same_day ? ['medicare', 'medicare_advantage'] : ['medicare']);
        const moved = Math.max(0, x.bx_patients) * med * clamp(x.bx_visit_share, 0, 1) * clamp(p.share, 0, 1);
        const lost = moved * (1 - clamp(p.return_rate, 0, 1));
        const back = moved - lost;
        // the rule's input: fewer biopsied Medicare patients with a visit the same day; those who never return lose the biopsy
        const vs = clamp(x.bx_visit_share, 0, 1) * (1 - clamp(p.share, 0, 1));
        const y = Object.assign({}, x, { bx_visit_share: vs, bx_patients: Math.max(0, x.bx_patients - lost) });
        const turn = num(A.minutes.turnover), perEnc = num(A.costs.per_encounter), days = x.days_per_week * x.weeks_per_year;
        const needs = [];
        if (turn == null) needs.push('minutes.turnover');
        if (perEnc == null) needs.push('costs.per_encounter');
        return { x: y, extra: { minutes: back * (turn || 0), encounters: back, cost: back * (perEnc || 0) * days * 100 },
                 notes: [`${back.toFixed(2)} extra procedure-only visits a clinic day; ${lost.toFixed(2)} biopsies a day not done because the patient did not return.`], needs };
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
      compliance: "Billed under the APP's own number it is ordinary practice. Billing it under the physician (incident to) needs the physician's plan of care, an established problem and the physician in the office suite.",
      params: [['visits', 'Established visits a day handed to an APP', 0, 20, 0.5, 'n1', 3],
               ['fill', 'Use the freed physician minutes for more Mohs cases', 0, 1, 1, 'bool', 1],
               ['app_rate', "The APP's visit paid, against the physician's", 0.5, 1, 0.01, 'pct', 0.85]],
      apply(d, x, A, p, cal) {
        const n = Math.min(Math.max(0, p.visits), Math.max(0, x.visits) * (1 - clamp(x.new_share, 0, 1)));
        const y = Object.assign({}, x, { visits: x.visits - n, new_share: x.visits - n > 0 ? x.visits * x.new_share / (x.visits - n) : 0 });
        const needs = [];
        const m = A.minutes, perVisit = num(m.visit_est);
        if (perVisit == null) needs.push('minutes.visit_est');
        if (p.fill) {
          const L = d.levers, perCase = num(m.mohs_case), stage = num(m.mohs_stage) || 0, cl = num(m.flap_graft), ly = num(m.layered);
          if (perCase == null) needs.push('minutes.mohs_case');
          if (perVisit != null && perCase != null) {
            const caseMin = perCase + Math.max(0, x.mohs_stages - 1) * stage + clamp(x.fg_share, 0, 2) * (cl || 0) + clamp(x.lay_mohs, 0, 2) * (ly || 0);
            y.mohs_cases = x.mohs_cases + n * perVisit / caseMin;
          }
        }
        // the APP's side: the same visits at the APP's rate, less what an APP visit costs
        const before = CM.evaluate(d, x, cal), days = x.days_per_week * x.weeks_per_year;
        const visitsYear = Math.max(1e-9, x.visits * days);
        const perVisitColl = ((before.byLine.visits || {}).collections || 0) / visitsYear;
        const appColl = n * days * perVisitColl * clamp(p.app_rate, 0, 1);
        const appCost = num(A.costs.app_per_visit);
        if (appCost == null) needs.push('costs.app_per_visit');
        return { x: y, extra: { collections: appColl, cost: n * days * (appCost || 0) * 100, minutes: 0 },
                 notes: [`${n.toFixed(1)} visits a day move to an APP${p.fill && y.mohs_cases !== x.mohs_cases ? `; the physician takes ${(y.mohs_cases - x.mohs_cases).toFixed(2)} more Mohs cases a day in the freed time` : ''}. The APP's visits are counted as the practice's, not this clinician's.`],
                 needs, practice_only: appColl };
      },
    },
    {
      id: 'b1', title: 'Bill the visit complexity add-on wherever it applies', tier: 'low',
      why: "G2211 (from 2027, modifier B1 at 16% of the visit) pays Medicare visits that are part of ongoing care for a serious or complex condition. It is not paid on a visit billed with modifier 25.",
      compliance: "Only where the note shows the longitudinal relationship; never on a visit with a same-day procedure (modifier 25), except preventive services, a wellness visit or a vaccine.",
      params: [['capture', 'Share of Medicare visits without a same-day procedure billed with it', 0, 1, 0.05, 'pct', 0.5],
               ['eligible', 'Share of Medicare visits with no same-day procedure', 0, 1, 0.05, 'pct', 0.6]],
      apply(d, x, A, p) {
        const med = kindShare(d, 'est_visit', ['medicare']);
        const per = med * clamp(p.eligible, 0, 1) * clamp(p.capture, 0, 1);
        const cur = (d.levers.visits.g2211_per_visit || 0);
        if (per <= cur) return { x, extra: {}, notes: [`Already billed on ${(cur * 100).toFixed(1)} of every 100 visits, at or above this target.`], needs: [] };
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
      compliance: 'Estimate from the payer\'s eligibility response; refund promptly when the remittance shows less owed.',
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
      why: 'Fees taken from payments by card and payment vendors (practice-wide, from the remittances). Every payer has to pay by plain ACH on request, without a vendor\'s fee.',
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
      apply(d, x, A, p, cal) {
        const full = CM.evaluate(d, x, cal).totalCollections, none = CM.evaluate(d, Object.assign({}, x, { corrected: {} }), cal).totalCollections;
        const months = 13 - clamp(Math.round(p.month), 1, 12);
        return { x, extra: { collections: -(full - none) * (1 - months / 12) }, notes: [`${months} months of the corrected contracts in the year.`], needs: [] };
      },
    },
  ];

  function monthsBetween(a, b) {
    if (!a || !b) return 12;
    return (+b.slice(0, 4) - +a.slice(0, 4)) * 12 + (+b.slice(5, 7) - +a.slice(5, 7)) + 1;
  }

  function paramDefaults(S) { return Object.fromEntries(S.params.map(p => [p[0], p[6]])); }

  // ------------------------------------------------------------------ run a scenario
  // chosen: {strategyId: {on: bool, params: {...}}}. Returns the plan before, each strategy's step, and the result.
  function run(d, x, A, chosen, cal) {
    A = filled(A);
    cal = cal || CM.calibrate(d);
    const next = d.next_year && (x.year === d.next_year);
    const sumUp = (y, extras) => {
      const ev = CM.evaluate(d, y, cal);
      const act = activity(d, y), days = ev.daysYear;
      const min = minutes(act, A), cost = costs(act, A, days);
      return { ev, act, minutes: min, costs: cost, wrvu: wrvus(d, y),
               collections: ev.totalCollections + extras.collections, cost: cost.total + extras.cost,
               minutesDay: min.total + extras.minutes, encountersDay: act.encounters + extras.encounters, oneTime: extras.one_time,
               practiceOnly: extras.practice_only };
    };
    const zero = { collections: 0, cost: 0, minutes: 0, encounters: 0, one_time: 0, practice_only: 0 };
    const base = sumUp(x, zero);
    let cur = x, extras = Object.assign({}, zero), prev = base;
    const steps = [];
    for (const S of STRATEGIES) {
      const c = chosen && chosen[S.id];
      if (!c || !c.on) continue;
      if (S.only === 'next' && !next) { steps.push({ id: S.id, title: S.title, skipped: 'Medicare\'s 2027 rules are not on: switch the calculator to Plan 2027.' }); continue; }
      if (S.applies && !S.applies(d, cur)) { steps.push({ id: S.id, title: S.title, skipped: 'Does not apply to this clinician\'s work.' }); continue; }
      const params = Object.assign(paramDefaults(S), c.params || {});
      const r = S.apply(d, cur, A, params, cal) || { x: cur, extra: {} };
      const e = Object.assign({}, zero, r.extra || {});
      for (const k of Object.keys(zero)) extras[k] += e[k] || 0;
      cur = r.x || cur;
      const now = sumUp(cur, extras);
      steps.push({ id: S.id, title: S.title, tier: S.tier, notes: r.notes || [], needs: r.needs || [],
                   collections: now.collections - prev.collections, cost: now.cost - prev.cost, minutesDay: now.minutesDay - prev.minutesDay,
                   encountersDay: now.encountersDay - prev.encountersDay, oneTime: e.one_time || 0, practiceOnly: e.practice_only || 0,
                   wrvu: now.wrvu - prev.wrvu, per_case: r.per_case });
      prev = now;
    }
    const days = prev.ev.daysYear;
    const session = num(A.session_minutes);
    return { base, steps, result: prev, x: cur,
             capacity: session ? { available: session, used: prev.minutesDay, share: prev.minutesDay / session } : null,
             perMinute: minutesValue(d, cur, A, cal), daysYear: days };
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

  const api = { MINUTES, COSTS, COMP_TYPES, STRATEGIES, blankAssumptions, filled, activity, minutes, costs, wrvus, pay, collectionsFor,
                run, minutesValue, paramDefaults, caseValue, BIGGER };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.LabModel = api;
})(typeof window !== 'undefined' ? window : globalThis);
