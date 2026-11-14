// Checks of the strategy lab (remitwatch/static/lab_model.js) and of the saving logic of its page (lab.js) on a made-up
// payload. Run by tests/test_lab_review.py with node; prints one JSON object {name: [ok, detail]}. Every clinician,
// payer and amount here is invented.
const M = require('../remitwatch/static/calc_model.js');
const LM = require('../remitwatch/static/lab_model.js');
const fs = require('fs'), path = require('path'), vm = require('vm');
const out = {};
const ok = (name, cond, detail) => { out[name] = [!!cond, detail == null ? '' : String(detail)]; };
const near = (a, b, tol) => Math.abs(a - b) <= (tol == null ? 1e-6 : tol) * Math.max(1, Math.abs(b));
const finiteDeep = (v, seen = new Set()) => {
  if (typeof v === 'number') return isFinite(v);
  if (!v || typeof v !== 'object' || seen.has(v)) return true;
  seen.add(v);
  return Object.values(v).every(w => finiteDeep(w, seen));
};

// two payers (Medicare, a commercial plan), office rates in cents; a flap, a graft, layered closures, visits, biopsies,
// radiation (an ancillary line) and pathology on the biopsies
function payload() {
  const rates = pairs => Object.fromEntries(Object.entries(pairs).map(([g, v]) => [g, { '2026': v, how: 'own' }]));
  const code = (family, lever, units) => ({ family, lever, units, allowed_cents: 0, units_by_year: { '2026': units } });
  const codes = {
    '17311': code('mohs', 'mohs_first', 100), '17312': code('mohs', 'mohs_stage', 50),
    '14040': code('repair', 'flap_graft', 20), '14021': code('repair', 'flap_graft', 10), '15260': code('repair', 'flap_graft', 10),
    '13132': code('repair', 'layered', 20),
    '99203': code('new_visit', 'visit_new', 50), '99213': code('est_visit', 'visit_est', 200), '99214': code('est_visit', 'visit_est', 100),
    'G2211': code('visit_addon', 'g2211', 30), '11102': code('biopsy', 'biopsy_first', 50), '11103': code('biopsy', 'biopsy_add', 25),
    '77600': code('radiation', 'other', 10),
  };
  const R = {
    '17311': [60000, 54000], '17312': [37000, 37000], '14040': [70000, 63000], '14021': [80000, 72000], '15260': [90000, 81000],
    '13132': [40000, 36000], '99203': [15000, 16000], '99213': [9000, 8000], '99214': [12800, 14000], 'G2211': [1600, 1500],
    '11102': [8700, 7000], '11103': [4500, 4000], '77600': [40000, null],
  };
  const d = {
    rate_year: '2026', next_year: '2027', years: ['2026'], tier: 'physician', who: { id: '1000000001', name: 'TEST DOC' },
    schedule: { clinic_days: 10, days_per_week: 4, weeks_per_year: 46, allowed_per_patient: 30000, clinic_day_allowed: { p50: 900000, p90: 1200000 } },
    levers: {
      mohs: { cases: 10, head_neck_share: 1, stages: 1.5, extra_head_neck: 0.5, extra_trunk: null, blocks: 0 },
      flap_graft: { share: 0.4, mix: { '14040': 0.5, '14021': 0.25, '15260': 0.25 }, add_ons: {} },
      layered: { mohs_share: 0.2, excision_share: 0.75, mix: { '13132': 1 }, add_ons: {}, weights: [0.21, 0.75] },
      excision: { per_day: 0, mix: {} },
      visits: { per_day: 30, new_share: 0.2, new_mix: { '99203': 1 }, est_mix: { '99213': 2 / 3, '99214': 1 / 3 }, g2211_per_visit: 0.1 },
      biopsy: { lesions: 7.5, per_patient: 1.5, first_mix: { '11102': 1 }, add_mix: { '11103': 1 } },
      ak: { lesions: 0, per_patient: 4, share_15_plus: 0.2 },
      benign: { patients: 0, share_15_plus: 0 },
      other: { codes: { '77600': 1 }, groups: { '77600': 'radiation' } },
    },
    codes,
    mix: { mohs: [['Medicare KY', 0.6], ['Plan A', 0.4]], repair: [['Medicare KY', 0.6], ['Plan A', 0.4]],
           new_visit: [['Medicare KY', 0.5], ['Plan A', 0.5]], est_visit: [['Medicare KY', 0.5], ['Plan A', 0.5]],
           visit_addon: [['Medicare KY', 0.5], ['Plan A', 0.5]], biopsy: [['Medicare KY', 0.5], ['Plan A', 0.5]],
           radiation: [['Medicare KY', 1]], _overall: [['Medicare KY', 0.5], ['Plan A', 0.5]] },
    rates: Object.fromEntries(Object.entries(R).map(([c, [m, a]]) => [c, rates(a == null ? { 'Medicare KY': m } : { 'Medicare KY': m, 'Plan A': a })])),
    medicare: Object.fromEntries(Object.entries(R).map(([c, [m]]) => [c, { '2026': m, '2027': Math.round(m * 0.95) }])),
    payers: { 'Medicare KY': { kind: 'medicare', correctable: false, indexed: true }, 'Plan A': { kind: 'commercial', correctable: true, indexed: false } },
    paid_share: { medicare: 0.95, commercial: 0.7 }, paid_share_fallback: 0.8, paid_share_class: {}, paid_share_all: null,
    pathology: { per_lesion_cents: 5000 },
    actual: { allowed_cents: 0, paid_cents: 0 },
    constants: { ak_lesions_15_plus: 20, correctable_kinds: ['commercial', 'medicare_advantage'], k_range: [0.25, 2],
                 min_k_units: 25, mppr_share: 0.5, app_medicare_share: 0.85 },
    add_on: ['17312', '11103', 'G2211'],
    mppr: ['17311', '14040', '14021', '15260', '13132', '11102'],
    work_rvu: { '2026': { '17311': 6.05, '17312': 3.0, '14040': 7.0, '14021': 8.0, '15260': 9.0, '13132': 3.0, '99213': 1.3, '99214': 1.9, '99203': 1.6 } },
    strategy: { open_findings: {}, vendor_fees: { 'Plan A': 6000 } },
  };
  return d;
}
const d = payload();
const cal = { k: {}, info: {} };
const x0 = Object.assign(M.defaults(d), { year: '2026', other_radiation: 1 });
const A = LM.blankAssumptions();
Object.assign(A.minutes, { mohs_case: 40, mohs_stage: 20, flap_graft: 30, layered: 15, visit_new: 20, visit_est: 10, turnover: 5 });
Object.assign(A.costs, { per_encounter: 20, per_closure: 50, app_per_visit: 40 });
const stepOf = (r, id) => r.steps.find(s => s.id === id);

// ------------------------------------------------------------------ figures as saved: only numbers reach the page
{
  const evil = '"><img src=x onerror=alert(1)>';
  const c = LM.clean({
    minutes: { mohs_case: evil, mohs_stage: 20, flap_graft: -5, layered: NaN, nonsense: 3 },
    costs: { per_encounter: '12', per_closure: 7.5 }, session_minutes: Infinity,
    comp: { '1000000001': { type: 'pct', pct: 0.35 }, '1000000002': { type: 'evil', pct: 0.3 }, '1000000003': { type: 'base', base: evil, above_pct: 2 },
            '<b>': { type: 'pct', pct: 0.3 } },
    scenarios: [{ name: ' Keep me ', who: 'mohs', x: { mohs_cases: 5, year: '2027', q: { commercial: 0.6 } }, chosen: { stage: { on: true, params: { share: evil, all: 1 } }, nope: { on: true } } },
                { name: '', who: 'mohs' }, { name: 'bad who', who: '../x' }, 'not an object'],
    __proto__evil: 1,
  });
  ok('clean_numbers_only', c.minutes.mohs_case === null && c.minutes.mohs_stage === 20 && c.minutes.flap_graft === null && c.minutes.layered === null
    && !('nonsense' in c.minutes) && c.costs.per_encounter === null && c.costs.per_closure === 7.5 && c.session_minutes === null, JSON.stringify(c.minutes) + JSON.stringify(c.costs));
  ok('clean_pay_plans', Object.keys(c.comp).join() === '1000000001,1000000003' && c.comp['1000000003'].base === null && c.comp['1000000003'].above_pct === null,
    JSON.stringify(c.comp));
  const s = c.scenarios;
  ok('clean_scenarios', s.length === 1 && s[0].name === 'Keep me' && s[0].chosen.stage.on === true && !('share' in s[0].chosen.stage.params)
    && s[0].chosen.stage.params.all === 1 && !('nope' in s[0].chosen) && s[0].x.q.commercial === 0.6, JSON.stringify(s));
  const proto = LM.clean(JSON.parse('{"minutes":{"__proto__":{"x":1}},"scenarios":[{"name":"p","who":"mohs","x":{"__proto__":{"polluted":1}}}]}'));
  ok('clean_proto_keys', ({}).polluted === undefined && proto.scenarios[0].x.polluted === undefined && Object.getPrototypeOf(proto.scenarios[0].x) === Object.prototype, '');
  const f = LM.filled(null);
  ok('filled_blank', Object.values(f.minutes).every(v => v === null) && Array.isArray(f.scenarios) && f.session_minutes === null, '');
}

// ------------------------------------------------------------------ pay counts the clinician's own production only
{
  const plan = { type: 'pct', pct: 0.4 };
  const off = LM.run(d, Object.assign({}, x0, { include_ancillary: false }), A, {}, cal);
  const on = LM.run(d, Object.assign({}, x0, { include_ancillary: true, include_path: true }), A, {}, cal);
  const ev = M.evaluate(d, x0, cal);
  ok('pay_own_production', near(on.result.ownProduction, off.result.ownProduction) && near(on.result.ownProduction, ev.collections)
    && on.result.collections > off.result.collections + 1, `${on.result.ownProduction} ${off.result.ownProduction} ${on.result.collections}`);
  ok('pay_not_on_ancillary', near(LM.pay(plan, on.result.ownProduction, 0), LM.pay(plan, ev.collections, 0)), '');
  ok('practice_only_holds_ancillary', near(on.result.practiceOnly, on.result.collections - on.result.ownProduction), '');
}

// ------------------------------------------------------------------ staging: the bigger closures include 14001 and 14021
ok('bigger_closures', ['14001', '14021', '14041', '14061', '15260'].every(LM.BIGGER) && !['14000', '14020', '14040', '14060'].some(LM.BIGGER), '');

// ------------------------------------------------------------------ staging: patients who do not come back lose the closure
{
  const run = ret => LM.run(d, x0, A, { stage: { on: true, params: { share: 1, return_rate: ret, full: 0 } } }, cal);
  const r1 = run(1), r5 = run(0.5), r0 = run(0);
  const s1 = stepOf(r1, 'stage'), s5 = stepOf(r5, 'stage'), s0 = stepOf(r0, 'stage');
  // bigger codes here: 14021 and 15260 (half of the flaps and grafts); paired share 1 (fg 0.4 + layered 0.2 < 1)
  const U = M.units(d, x0), moved = U['14021'] + U['15260'];
  const days = x0.days_per_week * x0.weeks_per_year;
  const full = M.evaluate(d, Object.assign({}, x0, { lay_mohs: 0, stage: { codes: ['14021', '15260'], share: 1 } }), cal);
  const fullColl = full.byCode['14021'].collections + full.byCode['15260'].collections;
  ok('stage_lost_closures', near(s1.collections - s5.collections, 0.5 * fullColl, 1e-6) && near(s1.collections - s0.collections, fullColl, 1e-6),
    `${s1.collections} ${s5.collections} ${s0.collections} ${fullColl}`);
  ok('stage_lost_can_lose_money', s0.collections < 0, s0.collections);
  ok('stage_every_patient_comes_back_once', near(s5.encountersDay, moved) && near(s0.encountersDay, moved), `${s5.encountersDay} ${moved}`);
  const wLost = (U['14021'] * 8 + U['15260'] * 9) * 0.5 * days;
  ok('stage_lost_wrvu_minutes_costs', near(s5.wrvu, -wLost, 1e-6) && near(s5.minutesDay, moved * 5 - moved * 0.5 * 30, 1e-6)
    && near(s5.cost, moved * 20 * days * 100 - moved * 0.5 * 50 * days * 100, 1e-6), `${s5.wrvu} ${s5.minutesDay} ${s5.cost}`);
  ok('stage_is_a_whatif', r1.result === r1.base || near(r1.result.collections, r1.base.collections), `${r1.result.collections} ${r1.base.collections}`);
  ok('stage_whatif_apart', r1.whatif && near(r1.whatif.collections - r1.result.collections, s1.collections), '');
  ok('stage_coinsurance_note', /coinsurance/.test(s1.notes[0]) && /second trip/.test(s1.notes[0]), s1.notes[0]);
}

// ------------------------------------------------------------------ another surgeon's closures leave this clinician; staging kept
{
  const both = LM.run(d, x0, A, { other_surgeon: { on: true, params: { share: 0.2, cost: 1000 } }, stage: { on: true, params: { share: 0.5, full: 0 } } }, cal);
  const stageAlone = LM.run(d, x0, A, { stage: { on: true, params: { share: 0.5, full: 0 } } }, cal);
  const os = stepOf(both, 'other_surgeon'), st = stepOf(both, 'stage');
  ok('other_surgeon_keeps_staging', both.whatifX && both.whatifX.stage && both.whatifX.stage.codes.join() === '14021,15260' && near(both.whatifX.stage.share, 0.5),
    JSON.stringify(both.whatifX && both.whatifX.stage));
  ok('other_surgeon_then_stage', near(st.collections, 0.8 * stepOf(stageAlone, 'stage').collections, 1e-6), `${st.collections} ${stepOf(stageAlone, 'stage').collections}`);
  const U = M.units(d, x0), days = x0.days_per_week * x0.weeks_per_year, wt = { '14040': 7, '14021': 8, '15260': 9 };
  const w = Object.keys(wt).reduce((t, c) => t + U[c] * wt[c], 0) * 0.2 * days;
  ok('other_surgeon_moves_wrvu_minutes', near(os.wrvu, -w, 1e-6) && near(os.minutesDay, -0.2 * (U['14040'] + U['14021'] + U['15260']) * 30, 1e-6), `${os.wrvu} ${-w} ${os.minutesDay}`);
  ok('other_surgeon_kept_is_practice', os.practiceOnly > 0 && near(both.result.ownProduction, both.result.collections - both.result.practiceOnly), os.practiceOnly);
  const noCost = LM.run(d, x0, A, { other_surgeon: { on: true } }, cal);
  ok('other_surgeon_cost_required', stepOf(noCost, 'other_surgeon').needs.includes('param.cost'), stepOf(noCost, 'other_surgeon').needs.join());
  const S = LM.STRATEGIES.find(s => s.id === 'other_surgeon');
  ok('other_surgeon_not_for_apps', !S.applies(Object.assign({}, d, { tier: 'nonphysician' }), x0) && S.tier === 'medium', '');
}

// ------------------------------------------------------------------ APP shift
{
  const fill = { app_shift: { on: true, params: { visits: 3, fill: 1 } } };
  const noMohs = LM.run(d, Object.assign({}, x0, { mohs_cases: 0 }), A, fill, cal);
  ok('app_no_mohs_no_fill', noMohs.x.mohs_cases === 0 && /does no Mohs/.test(stepOf(noMohs, 'app_shift').notes[0]), stepOf(noMohs, 'app_shift').notes[0]);
  const A0 = JSON.parse(JSON.stringify(A)); Object.assign(A0.minutes, { mohs_case: 0, mohs_stage: 0, flap_graft: 0, layered: 0 });
  const zero = LM.run(d, x0, A0, fill, cal);
  ok('app_zero_case_minutes_finite', finiteDeep(zero.result) && zero.x.mohs_cases === x0.mohs_cases, zero.x.mohs_cases);
  const filled = LM.run(d, x0, A, fill, cal);
  ok('app_fill_with_mohs', filled.x.mohs_cases > x0.mohs_cases, filled.x.mohs_cases);
  ok('app_fill_default_off', LM.paramDefaults(LM.STRATEGIES.find(s => s.id === 'app_shift')).fill === 0, '');
  // the moved visits valued at the established codes only (new visits pay more here)
  const r = LM.run(d, x0, A, { app_shift: { on: true, params: { visits: 3, fill: 0, app_rate: 1 } } }, cal);
  const ev = M.evaluate(d, x0, cal), days = x0.days_per_week * x0.weeks_per_year;
  const est = (ev.byCode['99213'].collections + ev.byCode['99214'].collections) / (x0.visits * (1 - x0.new_share) * days);
  ok('app_values_established_visits', near(stepOf(r, 'app_shift').practiceOnly, 3 * days * est, 1e-9), `${stepOf(r, 'app_shift').practiceOnly} ${3 * days * est}`);
}

// ------------------------------------------------------------------ the practice's money is never the clinician's production
{
  const r = LM.run(d, x0, A, { app_shift: { on: true, params: { fill: 0 } } }, cal);
  const app = stepOf(r, 'app_shift');
  ok('app_visits_not_own_production', app.practiceOnly > 0 && near(r.result.ownProduction, M.evaluate(d, r.x, cal).collections, 1e-9), `${r.result.ownProduction}`);
  const d2 = payload(); d2.strategy.vendor_fees = { 'Plan A': 6000 }; d2.strategy.vendor_fees_months = 6;
  const v = LM.run(d2, x0, A, { vendor_fees: { on: true, params: { share: 1 } } }, cal);
  ok('vendor_fees_a_year', near(stepOf(v, 'vendor_fees').collections, 12000) && near(stepOf(v, 'vendor_fees').practiceOnly, 12000)
    && near(v.result.ownProduction, v.base.ownProduction), stepOf(v, 'vendor_fees').collections);
  const d3 = payload(); d3.strategy.open_findings = { denial: { n: 538.3, cents: 1234500 } }; d3.strategy.open_findings_clinicians = 3;
  const a = stepOf(LM.run(d3, x0, A, { appeals: { on: true } }, cal), 'appeals');
  ok('appeals_role_average', /^538 open findings/.test(a.notes[0]) && /per clinician on average/.test(a.notes[0]), a.notes[0]);
}

// ------------------------------------------------------------------ stacking does not depend on the order
{
  const xd = Object.assign({}, x0, { days_per_week: x0.days_per_week + 1 });
  const a = LM.run(d, x0, A, { app_shift: { on: true, params: { fill: 1 } }, add_session: { on: true, params: { days: 1 } } }, cal).result;
  const b = LM.run(d, xd, A, { app_shift: { on: true, params: { fill: 1 } } }, cal).result;
  ok('stack_days_rescale_extras', near(a.collections, b.collections, 1e-9) && near(a.cost, b.cost, 1e-9) && near(a.ownProduction, b.ownProduction, 1e-9),
    `${a.collections} ${b.collections} ${a.cost} ${b.cost}`);
  const q = Object.fromEntries(Object.entries(x0.q).map(([k, v]) => [k, k === 'medicaid' ? 0 : Math.min(1, v + 0.3)]));
  const c = LM.run(d, x0, A, { app_shift: { on: true }, collect: { on: true, params: { points: 0.3 } } }, cal).result;
  const e = LM.run(d, Object.assign({}, x0, { q }), A, { app_shift: { on: true } }, cal).result;
  ok('stack_collect_rescales_extras', near(c.collections, e.collections, 1e-9), `${c.collections} ${e.collections}`);
  const st1 = LM.run(d, x0, A, { app_shift: { on: true, params: { fill: 1 } }, stage: { on: true, params: { share: 0.5 } } }, cal);
  const xm = Object.assign({}, x0, { mohs_cases: st1.x.mohs_cases, visits: st1.x.visits, new_share: st1.x.new_share });
  const st2 = LM.run(d, xm, A, { stage: { on: true, params: { share: 0.5 } } }, cal);
  ok('stack_stage_on_final_cases', near(stepOf(st1, 'stage').encountersDay, stepOf(st2, 'stage').encountersDay, 1e-9), '');
}

// ------------------------------------------------------------------ biopsies brought back: the same-day pool loses each moved patient once
{
  const x7 = Object.assign({}, x0, { year: '2027' });
  const r = LM.run(d, x7, A, { biopsy_return: { on: true, params: { share: 0.5, return_rate: 0.8 } } }, cal);
  const y = r.whatifX;
  ok('biopsy_pool_once', y && near(y.bx_patients * y.bx_visit_share, x7.bx_patients * x7.bx_visit_share * 0.5, 1e-9), y && `${y.bx_patients} ${y.bx_visit_share}`);
  const S = LM.STRATEGIES.find(s => s.id === 'biopsy_return');
  ok('biopsy_default_zero_whatif', LM.paramDefaults(S).share === 0 && S.whatif === true && /cost-sharing/.test(S.compliance) && /copay waivers/.test(S.compliance), '');
  ok('biopsy_patient_cost_note', /coinsurance/.test(stepOf(r, 'biopsy_return').notes[0]) && /diagnosis/.test(stepOf(r, 'biopsy_return').notes[0]), stepOf(r, 'biopsy_return').notes[0]);
}

// ------------------------------------------------------------------ contract start month
{
  const none = LM.run(d, x0, A, { contract_month: { on: true, params: { month: 12 } } }, cal);
  ok('contract_month_nothing_ticked', stepOf(none, 'contract_month').collections === 0 && /No contract/.test(stepOf(none, 'contract_month').notes[0]), stepOf(none, 'contract_month').notes[0]);
  const xc = Object.assign({}, x0, { corrected: { 'Plan A': true }, target_pct: 1.2 });
  const r = LM.run(d, xc, A, { contract_month: { on: true, params: { month: 12 } } }, cal);
  ok('contract_month_one_month', /^1 month of/.test(stepOf(r, 'contract_month').notes[0]), stepOf(r, 'contract_month').notes[0]);
  // the APP's visits priced at corrected contracts are prorated too
  const app = { app_shift: { on: true, params: { fill: 0 } } };
  const full = LM.run(d, xc, A, app, cal).result, plain = LM.run(d, Object.assign({}, xc, { corrected: {} }), A, app, cal).result;
  const jan = LM.run(d, xc, A, Object.assign({ contract_month: { on: true, params: { month: 7 } } }, app), cal).result;
  ok('contract_month_prorates_extras', near(jan.collections, plain.collections + (full.collections - plain.collections) * 0.5, 1e-9)
    && near(jan.ownProduction, plain.ownProduction + (full.ownProduction - plain.ownProduction) * 0.5, 1e-9), `${jan.collections} ${plain.collections} ${full.collections}`);
}

// ------------------------------------------------------------------ pay targets
{
  const base = { type: 'base', base: 300000, threshold: 900000, above_pct: 0.4 };
  ok('target_below_base', LM.collectionsFor(base, 25000000) === 0 && LM.planMissing(base, 25000000) === null, LM.collectionsFor(base, 25000000));
  ok('target_base_no_share', LM.collectionsFor({ type: 'base', base: 300000 }, 50000000) === null
    && LM.planMissing({ type: 'base', base: 300000 }, 50000000) === 'the share above the threshold', '');
  ok('target_pct_missing', LM.planMissing({ type: 'pct', pct: null }, 1) === 'the share of collections' && LM.planMissing({ type: 'wrvu' }, 1) === 'the rate per work RVU', '');
}

// ------------------------------------------------------------------ defaults and tiers the compliance review asked for
{
  const S = id => LM.STRATEGIES.find(s => s.id === id);
  ok('stage_default_baseline', LM.paramDefaults(S('stage')).share === 0.01 && S('stage').whatif === true && /NCCI/.test(S('stage').compliance), '');
  ok('b1_check_first', S('b1').tier === 'medium', '');
  ok('collect_qmb', /qualified Medicare beneficiary/.test(S('collect').compliance), '');
  ok('vendor_hipaa', /HIPAA-covered health plans/.test(S('vendor_fees').why), '');
}

// ------------------------------------------------------------------ everything on: no number that is not finite
{
  const all = Object.fromEntries(LM.STRATEGIES.map(s => [s.id, { on: true }]));
  const bad = [];
  for (const y of ['2026', '2027']) {
    for (const xx of [x0, Object.assign({}, x0, { mohs_cases: 0, visits: 0, days_per_week: 0 })]) {
      const r = LM.run(d, Object.assign({}, xx, { year: y }), A, all, cal);
      if (!finiteDeep({ s: r.steps, b: r.base.collections, c: r.result.collections, w: r.whatif && r.whatif.collections })) bad.push(y);
    }
  }
  ok('all_on_finite', !bad.length, bad.join());
}

// ------------------------------------------------------------------ the page's saving (lab.js), with the server stubbed
async function pageChecks() {
  const src = fs.readFileSync(path.join(__dirname, '..', 'remitwatch', 'static', 'lab.js'), 'utf8');
  const server = { doc: null, version: null, posts: 0, failRead: false, readError: null, n: 0 };
  const ver = () => 'v' + (++server.n);
  const api = async (p, opts) => {
    if (!opts || opts.method !== 'POST') {
      if (server.failRead) { const e = new Error('Storage is not connected yet.'); e.status = 503; throw e; }
      if (server.readError) return { assumptions: null, error: server.readError };
      return server.doc ? { assumptions: JSON.parse(JSON.stringify(server.doc)), version: server.version } : { assumptions: null };
    }
    server.posts++;
    const sent = opts.headers['X-Figures-Version'] || null;
    if (sent !== server.version) { const e = new Error('These figures were changed in another window.'); e.status = 409; throw e; }
    server.doc = Object.assign(JSON.parse(opts.body), { saved_at: '2026-07-01T00:00:00+00:00' }); server.version = ver();
    return { ok: true, saved_at: server.doc.saved_at, version: server.version };
  };
  const ctx = { console, LabModel: LM, CalcModel: M, api, state: { calc: {} }, esc: s => String(s), ROLE_LABEL: { mohs: 'Mohs surgeon', general: 'General dermatologist', apps: 'APP' },
                moneyK: c => String(c), money: c => String(c), calcFmt: {}, requestAnimationFrame: f => f() };
  vm.createContext(ctx);
  vm.runInContext(src, ctx);
  const cs = ctx.state.calc;

  // a read that fails leaves saving off: nothing is posted
  server.doc = { comp: { '1000000001': { type: 'pct', pct: 0.35 } }, scenarios: [{ name: 'Keep me', who: 'mohs', x: {}, chosen: {} }] }; server.version = ver();
  server.failRead = true;
  await ctx.labAssumptions();
  let threw = null;
  try { await ctx.labSave(doc => { doc.costs.per_encounter = 12; }); } catch (e) { threw = e.message; }
  ok('page_failed_read_blocks_save', cs.Aok === false && server.posts === 0 && /could not be read/.test(threw || '') && server.doc.comp['1000000001'], threw);
  server.failRead = false; server.readError = 'the saved figures could not be read';
  await ctx.labAssumptions();
  ok('page_read_error_blocks_save', cs.Aok === false, '');
  server.readError = null;
  await ctx.labAssumptions();
  ok('page_read_again', cs.Aok === true && cs.A.comp['1000000001'].pct === 0.35, '');

  // another window saves a scenario and a pay plan; this one then saves a figure: the change is applied to a fresh read
  server.doc.scenarios.push({ name: 'From the other tab', who: 'mohs', x: {}, chosen: {} });
  server.doc.comp['1000000003'] = { type: 'base', base: 200000 };
  server.version = ver();
  const posts = server.posts;
  const r = await ctx.labSave(doc => { doc.costs.per_encounter = 12; });
  ok('page_stale_save_rebased', r.rebased === true && server.posts === posts + 2 && server.doc.costs.per_encounter === 12
    && server.doc.scenarios.map(s => s.name).join() === 'Keep me,From the other tab' && server.doc.comp['1000000003'].base === 200000
    && server.doc.comp['1000000001'].pct === 0.35, JSON.stringify(server.doc));
  // deleting one scenario by name keeps the rest
  await ctx.labSave(doc => { doc.scenarios = doc.scenarios.filter(s => s.name !== 'Keep me'); });
  ok('page_delete_by_name', server.doc.scenarios.map(s => s.name).join() === 'From the other tab', '');

  // a saved plan is taken back only where each setting has the right kind of value
  const p = ctx.labPlanFrom(d, { mohs_cases: '"><img>', visits: 12, year: '1999', fix_mode: '<b>', q: { commercial: 0.9, medicare: 'x', evil: 1 },
                                 corrected: { 'Plan A': true, 'Nobody': true }, extra_key: 1 });
  ok('page_plan_types', p.mohs_cases === M.defaults(d).mohs_cases && p.visits === 12 && p.year === M.defaults(d).year && p.fix_mode === M.defaults(d).fix_mode
    && p.q.commercial === 0.9 && p.q.medicare === M.defaults(d).q.medicare && !('evil' in p.q) && p.corrected['Plan A'] === true && !('Nobody' in p.corrected)
    && !('extra_key' in p), JSON.stringify(p));
  ok('page_target_amounts', ctx.sheetAmount('650k') === 65000000 && ctx.sheetAmount('1.6m') === 160000000 && ctx.sheetAmount('$650,000') === 65000000
    && isNaN(ctx.sheetAmount('abc')), '');
}

pageChecks().catch(e => ok('page_checks_ran', false, e.stack)).then(() => process.stdout.write(JSON.stringify(out)));
