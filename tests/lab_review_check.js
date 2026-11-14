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

// ------------------------------------------------------------------ the ASC: Mohs in the clinic, closures and excisions may move
// invented amounts: Medicare's office and facility amounts, ASC rates already wage-adjusted, and the cases a year counted
function ascPayload() {
  const e = payload();
  const med = { '17311': [60000, 25000], '17312': [35000, 15000], '14040': [66000, 48000], '15260': [88000, 65000], '13132': [40000, 22000],
                '13133': [15000, 9000], '11643': [30000, 17000], '12041': [25000, 14000] };
  const rows = { '17311': ['P2', true, { newburgh: 40000 }], '17313': ['P2', true, { newburgh: 40000 }], '17312': ['N1', false, { newburgh: null }],
                 '14040': ['A2', true, { newburgh: 100000 }], '15260': ['A2', true, { newburgh: 100000 }], '13132': ['A2', true, { newburgh: 20000 }],
                 '13133': ['N1', false, { newburgh: null }], '11643': ['P3', true, { newburgh: 17000 }], '12041': ['P3', false, { newburgh: 16000 }] };
  e.asc = { '2026': rows, '2027': rows, site: { office: 'newburgh', state: 'IN', label: 'Newburgh' },
            medicare: Object.fromEntries(Object.entries(med).map(([c, v]) => [c, { '2026': v, '2027': v }])),
            mppr: ['17311', '14040', '15260', '13132', '11643', '12041'], add_on: ['17312', '13133'], ascqr: { '2026': 0.98, '2027': 0.98 },
            cases: { from: 'provider_payers', clinic_days: 10,
                     mohs: [['14040 17311', 'clinic', 'medicare', 'IN', 4, 2], ['14040 17311', 'asc', 'medicare', 'IN', 6, 3],
                            ['13132 13133 17311', 'clinic', 'medicare', 'IN', 2, 0], ['15260 17311', 'asc', 'medicare', 'IN', 2, 1],
                            ['14040 17311', 'clinic', 'medicare_advantage', 'IN', 2, 0], ['14040 17311', 'clinic', 'medicare', 'KY', 5, 0],
                            ['14040 17311', 'clinic', 'medicaid', 'IN', 1, 0]],
                     excision: [['11643 13132', 'clinic', 'medicare', 'IN', 3, 1], ['11643 13132', 'clinic', 'medicare', 'IN', 2, 0]] } };
  e.payers['Plan M'] = { kind: 'medicare_advantage', correctable: true, indexed: true };
  e.payers['State plan'] = { kind: 'medicaid', correctable: false, indexed: false };
  return e;
}
{
  const e = ascPayload(), Y = '2026';
  const per = 4 * 46 / 10;                 // a case counted in the window, a year on this plan (184 clinic days over 10)
  const coll = c => M.collectShare(e, x0, 'Medicare KY', c);
  // Mohs never earns an ASC fee, though its row is on CMS's list
  ok('asc_no_mohs_fee', LM.ascFee(e, Y, [['17311', 1], ['17313', 1], ['17312', 2]]) === 0 && LM.ascCase(e, Y, 'mohs', '14040 17311').fee === 100000, '');
  // one ranking across the clinic claim and the ASC claim, each line at its own setting's amount
  const f = LM.ascCase(e, Y, 'mohs', '14040 17311');
  ok('asc_cross_claim_ranking', f.A === 66000 + 30000 && f.B === 60000 + 24000 && f.C === 60000 + 48000, JSON.stringify(f));
  // a graft at the facility amount outranks the clinic's Mohs: the Mohs is the line cut
  const g = LM.ascCase(e, Y, 'mohs', '15260 17311');
  ok('asc_graft_flip', g.B === 65000 + 30000 && g.A === 88000 + 30000, JSON.stringify(g));
  // an add-on is never cut, and the ASC is paid nothing for it (N1): the repair alone, in full
  const l = LM.ascCase(e, Y, 'mohs', '13132 13133 17311');
  ok('asc_add_on_n1', l.A === 60000 + 20000 + 15000 && l.B === 60000 + 11000 + 9000 && l.fee === 20000, JSON.stringify(l));
  // extra stages stay in the clinic in every option, never reduced
  const st = LM.ascCase(e, Y, 'mohs', '14040 17311', { stages: 1 });
  ok('asc_stages_in_clinic', st.A - f.A === 35000 && st.B - f.B === 35000 && st.C - f.C === 35000, JSON.stringify(st));
  // an excision and its repair in the ASC: the ASC's discount (the highest in full, the other at half); not discounted: in full
  const x1 = LM.ascCase(e, Y, 'excision', '11643 13132');
  ok('asc_discount_excision_repair', x1.A === 40000 + 15000 && x1.B === 22000 + 8500 && x1.fee === 20000 + 8500
     && LM.ascFee(e, Y, [['11643', 1], ['12041', 1]]) === 17000 + 16000, JSON.stringify(x1));
  // a payer that does not cut across the two claims; ASCQR on A2 rows only (P3 unchanged)
  const nc = LM.ascCase(e, Y, 'mohs', '14040 17311', { cross: false });
  ok('asc_no_cross_option', nc.B === 60000 + 48000 && nc.C === nc.B, JSON.stringify(nc));
  ok('asc_ascqr', LM.ascCase(e, Y, 'mohs', '14040 17311', { ascqr: true }).fee === 98000 && LM.ascCase(e, Y, 'excision', '11643 13132', { ascqr: true }).fee === 19600 + 8500, '');
  // the 2027 same-day visit rule (every case with a visit here): the ASC's lines only with the open switch on
  const off = LM.ascCase(e, Y, 'mohs', '14040 17311', { em: 1 }), on = LM.ascCase(e, Y, 'mohs', '14040 17311', { em: 1, emAsc: true });
  ok('asc_em_rule_switch', off.A === 96000 - 6000 && off.B === 84000 && on.B === 84000 - 4800 && on.A === off.A && off.C === 108000, JSON.stringify([off, on]));

  const run = params => LM.run(e, x0, A, { asc: { on: true, params } }, cal);
  const step = r => stepOf(r, 'asc');
  // nothing set: nothing moves, the routing now is shown, the share of the fee and the other plans' fee are asked for
  const r0 = step(run({}));
  ok('asc_baseline_nothing_moves', r0.collections === 0 && r0.detail.moved === 0 && r0.needs.includes('param.own') && r0.needs.includes('param.commercial_pct'), JSON.stringify(r0.needs));
  const curPro = per * (6 * (f.B - f.A) * coll('14040') + 2 * (g.B - g.A) * coll('15260')), curFee = per * (6 * 100000 * coll('14040') + 2 * 100000 * coll('15260'));
  ok('asc_current_routing', near(r0.detail.current.n, 8 * per) && near(r0.detail.current.pro, curPro, 1e-9) && near(r0.detail.current.fee, curFee, 1e-9)
     && near(r0.detail.pool.n, 17 * per) && near(r0.detail.pool.asc, 8 * per), JSON.stringify(r0.detail.current));      // Medicaid's counted, not moved
  // the change set: only Indiana's Medicare closures done in the clinic move (Kentucky, Medicaid and, until their fee is entered,
  // Medicare Advantage stay); the share of the fee not entered: the year counts none of it, both views shown
  const r1 = step(run({ to_asc: 1 }));
  const pro1 = per * (4 * (f.B - f.A) * coll('14040') + 2 * (l.B - l.A) * coll('13132')), fee1 = per * (4 * 100000 * coll('14040') + 2 * 20000 * coll('13132'));
  ok('asc_moves_clinic_rows_only', near(r1.detail.moved, 6 * per) && near(r1.collections, pro1, 1e-9) && near(r1.detail.withFee, pro1 + fee1, 1e-9)
     && r1.practiceOnly === 0 && /With the whole fee/.test(r1.notes.join(' ')), `${r1.collections} ${pro1}`);
  ok('asc_cost_asked', r1.needs.includes('costs.asc_per_case') && /Gross only/.test(r1.notes.join(' ')), r1.needs.join());
  // the practice keeps all of the fee: the fee is the practice's, the surgeon's own production changes by the professional fees only
  const A2 = LM.filled(A); A2.costs.asc_per_case = 300; A2.costs.office_per_case_saved = 50;
  const r2 = step(LM.run(e, x0, A2, { asc: { on: true, params: { to_asc: 1, own: 1 } } }, cal));
  ok('asc_owner_fee_is_practice_only', near(r2.collections, pro1 + fee1, 1e-9) && near(r2.practiceOnly, fee1, 1e-9) && near(r2.ownProduction, pro1, 1e-9)
     && !r2.needs.includes('param.own') && near(r2.cost, 6 * per * (300 - 50) * 100, 1e-9), JSON.stringify([r2.collections, r2.practiceOnly, r2.ownProduction, r2.cost]));
  const half = step(LM.run(e, x0, A2, { asc: { on: true, params: { to_asc: 1, own: 0.5 } } }, cal));
  ok('asc_owner_share', near(half.practiceOnly, fee1 / 2, 1e-9) && near(half.cost, 6 * per * (150 - 50) * 100, 1e-9), '');
  // closures brought back to the clinic, scoped to flaps and grafts
  const rb = step(run({ to_clinic: 1, layered: 0, own: 0 }));
  ok('asc_bring_back', near(rb.detail.back, 8 * per) && near(rb.collections, -curPro, 1e-9) && rb.practiceOnly === 0, `${rb.collections} ${-curPro}`);
  // Medicare Advantage once its fee share is entered, and its own cross-claim switch
  const ma = step(run({ to_asc: 1, own: 1, commercial_pct: 0.9 })), maOff = step(run({ to_asc: 1, own: 1, commercial_pct: 0.9, ma_cross: 0 }));
  const collMA = M.collectShare(e, x0, 'Plan M', '14040');
  ok('asc_ma_priced_when_entered', near(ma.detail.moved, 8 * per) && near(ma.collections - r2.collections, 2 * per * ((f.B - f.A) + 0.9 * 100000) * collMA, 1e-6)
     && !ma.needs.includes('param.commercial_pct'), `${ma.collections - r2.collections}`);
  ok('asc_ma_cross_switch', near(maOff.collections - ma.collections, 2 * per * (nc.B - f.B) * collMA, 1e-6), `${maOff.collections - ma.collections}`);
  // excisions: a physician's only (an APP may not operate in an ASC), and not for an APP at all
  const ex = step(run({ excisions: 1, own: 1 }));
  ok('asc_excisions_physician_only', near(ex.detail.excisions, 3 * per) && near(ex.collections, 3 * per * (x1.B + x1.fee - x1.A) * coll('13132'), 1e-9), JSON.stringify(ex.detail.excisions));
  const S = LM.STRATEGIES.find(z => z.id === 'asc');
  ok('asc_not_for_apps', !S.applies(Object.assign({}, e, { tier: 'nonphysician' }), x0) && S.applies(e, x0) && !S.applies(d, x0), '');
  // per closure, Medicare's amounts a case, with the margin warning on the small fees
  const types = Object.fromEntries(r0.detail.types.map(t => [t.type, t]));
  ok('asc_types', near(types.flap.owner, f.B + f.fee - f.A) && near(types.flap.nonOwner, f.B - f.A) && near(types.flap.coins, 0.2 * (f.B + f.fee - f.A))
     && near(types.flap.delay, f.C - f.B) && types.complex.lowMargin && !types.flap.lowMargin && near(types.flap.asc, 6 * per) && near(types.flap.clinic, 4 * per + 2 * per + per),
     JSON.stringify(types.flap));
  // the patient's extra coinsurance, uncapped, in the notes
  ok('asc_coinsurance_note', /pays about \$\d+ more per case moved/.test(r1.notes.join(' ')) && /not capped/.test(r1.notes.join(' ')), r1.notes.join(' '));
  // the later day: a what-if on the closures in the ASC, the gain all from the delay, never in the plan's total
  const L2 = LM.STRATEGIES.find(z => z.id === 'asc_later');
  ok('asc_later_defaults', L2.whatif && L2.tier === 'high' && LM.paramDefaults(L2).share === 0 && /highly problematic/.test(L2.compliance) && /NCCI/.test(L2.compliance), '');
  const lr = LM.run(e, x0, A, { asc_later: { on: true, params: { share: 1 } } }, cal), ls = stepOf(lr, 'asc_later');
  const gain = per * (6 * (f.C - f.B) * coll('14040') + 2 * (g.C - g.B) * coll('15260'));
  ok('asc_later_delay_only', near(ls.collections, gain, 1e-9) && near(lr.result.collections, lr.base.collections) && near(lr.whatif.collections - lr.result.collections, gain, 1e-9)
     && /delay|same-day cut/.test(ls.notes[0]), `${ls.collections} ${gain}`);
  // with the plan's moves (flaps and grafts only): the flaps moved to the ASC can be done later too; those brought back, and the
  // layered repair left in the clinic, cannot
  const lr2 = stepOf(LM.run(e, x0, A, { asc: { on: true, params: { to_asc: 1, to_clinic: 1, layered: 0 } }, asc_later: { on: true, params: { share: 1 } } }, cal), 'asc_later');
  ok('asc_later_follows_plan', near(lr2.collections, per * 4 * (f.C - f.B) * coll('14040'), 1e-9), lr2.collections);
  // the words: plain, no em dashes, and the compliance points
  const words = [S.why, S.compliance, ...S.params.map(q => q[1]), L2.why, L2.compliance, ...L2.params.map(q => q[1]), ...r1.notes, ...r2.notes, ...rb.notes, ...ls.notes];
  ok('asc_no_em_dash', words.every(w => !/[\u2014\u2013]/.test(w)), words.find(w => /[\u2014\u2013]/.test(w)));
  ok('asc_compliance', ['14 sec. 20.1', '416.42', '1001.952(r)', 'distinct entity', 'Indiana license', 'modifier 57', 'Kentucky', 'in proportion to capital'].every(t => S.compliance.includes(t)), '');
  ok('asc_own_label', S.params.find(q => q[0] === 'own')[1] === "Share of the ASC's facility fee the practice keeps (by capital)" && LM.paramDefaults(S).own === null
     && LM.paramDefaults(S).commercial_pct === null && LM.paramDefaults(S).em_rule === 0 && LM.paramDefaults(S).ma_cross === 1, '');
  // the open switch at the strategy: only on Medicare's 2027 rules, and it lowers what a same-day move brings
  const em = (y, on) => step(LM.run(e, Object.assign({}, x0, { year: y, mohs_visit_share: 1 }), A, { asc: { on: true, params: { to_asc: 1, em_rule: on } } }, cal)).collections;
  ok('asc_em_rule_2027_only', em('2027', 1) < em('2027', 0) && near(em('2026', 1), em('2026', 0)), [em('2027', 1), em('2027', 0)].join(' '));
  // everything on, both years, with the ASC's cases: finite
  const all = Object.fromEntries(LM.STRATEGIES.map(z => [z.id, { on: true, params: z.id === 'asc' ? { to_asc: 0.5, to_clinic: 0.2, excisions: 1, own: 0.4, commercial_pct: 1, em_rule: 1, ascqr: 1 } : {} }]));
  const bad = ['2026', '2027'].filter(y => { const r = LM.run(e, Object.assign({}, x0, { year: y }), A2, all, cal); return !finiteDeep({ s: r.steps, c: r.result.collections, w: r.whatif && r.whatif.collections }); });
  ok('asc_all_on_finite', !bad.length, bad.join());
}

// ------------------------------------------------------------------ re-check fixes (items numbered as in the review)
// every practice figure entered, so only what a strategy needs can leave a cost open
const Af = LM.filled(A);
Object.assign(Af.costs, { per_clinic_day: 2000, per_encounter: 20, per_mohs_case: 100, per_stage: 50, per_closure: 50, per_excision: 40, per_biopsy: 10, app_per_visit: 40 });
{
  // 1: a strategy's own cost not entered leaves the year's costs open (no 'left after costs'), as a missing figure does
  const base = LM.run(d, x0, Af, {}, cal);
  const noCost = LM.run(d, x0, Af, { other_surgeon: { on: true } }, cal), withCost = LM.run(d, x0, Af, { other_surgeon: { on: true, params: { cost: 250000 } } }, cal);
  ok('i1_strategy_cost_open', !LM.costsOpen(base.result, base.needs) && noCost.needs.includes('param.cost') && LM.costsOpen(noCost.result, noCost.needs)
     && !LM.costsOpen(withCost.result, withCost.needs), JSON.stringify([base.result.costs.missing, noCost.needs, withCost.needs]));
  const Ano = LM.filled(Af); Ano.costs.app_per_visit = null;
  const app = LM.run(d, x0, Ano, { app_shift: { on: true, params: { fill: 0 } } }, cal);
  ok('i1_app_cost_open', LM.costsOpen(app.result, app.needs) && app.needs.includes('costs.app_per_visit'), app.needs.join());
  const e = ascPayload(), asc = LM.run(e, x0, Af, { asc: { on: true, params: { to_asc: 1, own: 1 } } }, cal);
  ok('i1_asc_cost_open', asc.needs.includes('costs.asc_per_case') && LM.costsOpen(asc.result, asc.needs), asc.needs.join());
  // the what-ifs' own needs are kept apart from the plan's
  const st = LM.run(d, x0, LM.filled(A), { stage: { on: true, params: { share: 0.5 } } }, cal);
  ok('i1_whatif_needs_apart', !st.needs.length && Array.isArray(st.whatifNeeds), JSON.stringify([st.needs, st.whatifNeeds]));
}
{
  // 2: the practice's share of the ASC's fee not entered: the year takes the lower view. Closures brought back to the
  // clinic lose the whole fee; moved in, they bring none of it. The surgeon's own fees count either way.
  const e = ascPayload(), step = params => stepOf(LM.run(e, x0, A, { asc: { on: true, params } }, cal), 'asc');
  const back = step({ to_clinic: 1 }), backAll = step({ to_clinic: 1, own: 1 }), into = step({ to_asc: 1 });
  ok('i2_asc_back_lower_view', back.detail.fees < 0 && near(back.collections, back.detail.withFee, 1e-9) && near(back.collections, backAll.collections, 1e-9)
     && near(back.practiceOnly, back.detail.fees, 1e-9) && near(back.ownProduction, back.detail.pro, 1e-9) && /lower of the two views \(the whole fee lost\)/.test(back.notes.join(' ')),
     JSON.stringify([back.collections, back.detail.withFee, back.detail.withoutFee, back.practiceOnly]));
  ok('i2_asc_into_lower_view', near(into.collections, into.detail.withoutFee, 1e-9) && into.practiceOnly === 0 && /none of the fee/.test(into.notes.join(' ')), into.collections);
}
{
  // 5: the cut falls on the Mohs first stage when the closure's facility amount is the higher
  const S = LM.STRATEGIES.find(z => z.id === 'asc');
  ok('i5_asc_cut_wording', /to the closure, or to the Mohs first stage when the closure's facility amount is the higher/.test(S.why) && !/is still cut 50% against the clinic Mohs/.test(S.why), S.why);
}
{
  // 6: the visit add-on is raised at Medicare (and the Medicare Advantage plans following it) only. Here half the visits
  // and half the add-ons are Medicare's: 10 add-ons per 100 visits now, 5 at Medicare; the target 0.5 x 0.6 x 0.5 = 15 per
  // 100 visits there, so Medicare's add-ons triple and Plan A's stay as they are
  const r = LM.run(d, x0, A, { b1: { on: true } }, cal), s = stepOf(r, 'b1');
  const e1 = M.evaluate(d, x0, cal), e3 = M.evaluate(d, Object.assign({}, x0, { g2211_scale: 3 }), cal);
  const want = e3.byPayer['Medicare KY'].collections - e1.byPayer['Medicare KY'].collections;
  ok('i6_b1_medicare_only', want > 0 && near(s.collections, want, 1e-9) && r.x.g2211_scale == null && /Commercial and other plans are left as they are/.test(s.notes[0]),
     `${s.collections} ${want} ${s.notes[0]}`);
  const S = LM.STRATEGIES.find(z => z.id === 'b1');
  ok('i6_b1_wording', /the Medicare Advantage plans the Calculator has following Medicare/.test(S.compliance), S.compliance);
  // a clinician whose add-ons are all at commercial plans: nothing to raise at Medicare from history
  const dc = payload(); dc.mix.visit_addon = [['Plan A', 1]];
  ok('i6_b1_no_medicare_history', stepOf(LM.run(dc, x0, A, { b1: { on: true } }, cal), 'b1').collections === 0, '');
}
{
  // 14: added clinic days never take the plan past the Calculator's 6 days a week
  const x5 = Object.assign({}, x0, { days_per_week: 5 });
  const r = LM.run(d, x5, A, { add_session: { on: true, params: { days: 3 } } }, cal), r2 = LM.run(d, x5, A, { add_session: { on: true, params: { days: 0.5 } } }, cal);
  ok('i14_add_session_capped', r.x.days_per_week === 6 && /Capped at 6 clinic days a week/.test(stepOf(r, 'add_session').notes[0])
     && r2.x.days_per_week === 5.5 && !stepOf(r2, 'add_session').notes.length && LM.CAP.days_per_week === 6, JSON.stringify([r.x.days_per_week, stepOf(r, 'add_session').notes]));
}
{
  // 20, 21 and change A: the stage what-if stages the clinic's closures only (the routing from the payload's cases, then the
  // ASC strategy's moves); the closures done in the ASC belong to the later-day ASC what-if, so no delay is counted twice
  const e = ascPayload(), all = { share: 1, all: 1, full: 0 };
  const r = LM.run(e, x0, A, { stage: { on: true, params: all } }, cal), s = stepOf(r, 'stage');
  // in the ASC now, a clinic day: 14040 on 6 cases and 15260 on 2, over 10 clinic days; every flap and graft is paired here
  const U = M.units(e, x0), inAsc = { '14040': 0.6, '15260': 0.2 }, e0 = M.evaluate(e, x0, cal).totalCollections;
  let want = 0, moved = 0;
  for (const c of ['14040', '14021', '15260']) {
    const g = M.evaluate(e, Object.assign({}, x0, { stage: { codes: [c], share: 1 } }), cal).totalCollections - e0;
    const f = (inAsc[c] || 0) / U[c];
    want += (1 - f) * g; moved += U[c] * (1 - f);
  }
  ok('i20_stage_clinic_closures_only', near(s.collections, want, 1e-9) && near(s.encountersDay, moved, 1e-9) && /left to the later-day ASC what-if/.test(s.notes[0]),
     `${s.collections} ${want} ${s.encountersDay} ${moved}`);
  // one owner for each closure: stage's closures and the later-day ASC what-if's add up to every paired flap and graft
  const plan = LM.ascPlan(LM.paramDefaults(LM.STRATEGIES.find(z => z.id === 'asc'))), days = x0.days_per_week * x0.weeks_per_year;
  const later = LM.ascBook(e, x0, plan).rows.reduce((t, row) => t + (row.what === 'mohs' && row.priced ? row.perYear * LM.ascOnPlan(plan, row) : 0), 0) / days;
  ok('i20_single_owner', near(s.encountersDay + later, U['14040'] + U['14021'] + U['15260'], 1e-9), `${s.encountersDay} ${later}`);
  const L1 = LM.run(e, x0, A, { asc_later: { on: true, params: { share: 1 } } }, cal);
  const both = LM.run(e, x0, A, { stage: { on: true, params: all }, asc_later: { on: true, params: { share: 1 } } }, cal);
  ok('i20_both_ticked', near(stepOf(both, 'stage').collections, s.collections, 1e-9) && near(stepOf(both, 'asc_later').collections, stepOf(L1, 'asc_later').collections, 1e-9)
     && near(both.whatif.collections - both.result.collections, s.collections + stepOf(L1, 'asc_later').collections, 1e-9), '');
  // the routing: the ASC's flaps and grafts brought back to the clinic become stage's; moved into the ASC, they leave it
  const noAsc = payload(); noAsc.mix = e.mix; noAsc.payers = e.payers;
  const plain = stepOf(LM.run(noAsc, x0, A, { stage: { on: true, params: all } }, cal), 'stage');
  const back = stepOf(LM.run(e, x0, A, { asc: { on: true, params: { to_clinic: 1, own: 0 } }, stage: { on: true, params: all } }, cal), 'stage');
  const into = stepOf(LM.run(e, x0, A, { asc: { on: true, params: { to_asc: 1, own: 0 } }, stage: { on: true, params: all } }, cal), 'stage');
  ok('i21_stage_follows_routing', near(back.collections, plain.collections, 1e-9) && near(back.encountersDay, plain.encountersDay, 1e-9)
     && into.encountersDay < s.encountersDay - 0.39 && into.collections < s.collections, JSON.stringify([back.collections, plain.collections, into.encountersDay, s.encountersDay]));
  // the later-day ASC what-if: patients who do not come back heal open (the Mohs alone, in full; the closure and the ASC's
  // fee lost); the surgeon's rooming time for the second visit, no clinic slot. Per case (with half a stage): 14040 C - B
  // = 240.00, M - B = -240.00; 15260 C - B = 300.00, M - B = -350.00; the ASC's fee 1,000.00 each
  const per = 4 * 46 / 10, coll = c => M.collectShare(e, x0, 'Medicare KY', c);
  const lr = stepOf(LM.run(e, x0, A, { asc_later: { on: true, params: { share: 1, return_rate: 0.5 } } }, cal), 'asc_later');
  const half = 0.5 * per, gain = half * (6 * (24000 - 24000) * coll('14040') + 2 * (30000 - 35000) * coll('15260'));
  const feeLost = half * (6 * 100000 * coll('14040') + 2 * 100000 * coll('15260'));
  ok('i20_asc_later_return_rate', near(lr.collections, gain - feeLost, 1e-9) && near(lr.practiceOnly, -feeLost, 1e-9) && near(lr.minutesDay, (8 * half * 5 - 8 * half * 30) / days, 1e-9)
     && lr.encountersDay === 0 && /do not come back/.test(lr.notes[0]) && /no clinic slot/.test(lr.notes[0]), JSON.stringify([lr.collections, gain - feeLost, lr.minutesDay]));
  const LS = LM.STRATEGIES.find(z => z.id === 'asc_later');
  ok('i20_asc_later_defaults', LM.paramDefaults(LS).return_rate === 1 && /no delay is counted twice/.test(LS.why), '');
}
{
  // 36: plain words: one clinician is not an average; minus signs, not hyphens, before money
  const d1 = payload(); d1.strategy.open_findings = { denial: { n: 10, cents: 100000 } }; d1.strategy.open_findings_clinicians = 1;
  const a = stepOf(LM.run(d1, x0, A, { appeals: { on: true } }, cal), 'appeals');
  ok('i36_one_clinician', !/clinicians\)/.test(a.notes[0]) && !/on average/.test(a.notes[0]), a.notes[0]);
  const e = ascPayload(), b = stepOf(LM.run(e, x0, A, { asc: { on: true, params: { to_clinic: 1 } } }, cal), 'asc');
  ok('i36_minus_sign', /−\$\d/.test(b.notes.join(' ')) && !/-\$\d/.test(b.notes.join(' ')), b.notes.join(' '));
}

// ------------------------------------------------------------------ Kentucky closures to the Newburgh ASC
// Medicare's Kentucky amounts (invented), a Kentucky complex repair and a Kentucky Medicare Advantage flap besides the
// Kentucky Medicare flap of ascPayload ('14040 17311', 5 cases)
function kyPayload() {
  const e = ascPayload();
  const ky = { '17311': [59000, 24000], '17312': [34000, 14000], '14040': [65000, 47000], '15260': [87000, 64000], '13132': [39000, 21000], '13133': [14500, 8800] };
  e.asc.medicare_ky = Object.fromEntries(Object.entries(ky).map(([c, v]) => [c, { '2026': v, '2027': v }]));
  e.asc.cases.mohs.push(['13132 13133 17311', 'clinic', 'medicare', 'KY', 3, 0], ['14040 17311', 'clinic', 'medicare_advantage', 'KY', 4, 0]);
  return e;
}
{
  const per = 4 * 46 / 10, coll = c => M.collectShare(ascPayload(), x0, 'Medicare KY', c);
  const S = LM.STRATEGIES.find(z => z.id === 'asc');
  const stepIn = (e, params) => stepOf(LM.run(e, x0, A, { asc: { on: true, params } }, cal), 'asc');
  // not in the payload: the option is shown as not available and counts nothing
  const e0 = ascPayload(), na = stepIn(e0, { ky_share: 1, own: 1 }), none = stepIn(e0, { own: 1 });
  ok('B_ky_not_available', near(na.collections, none.collections, 1e-9) && na.detail.ky.available === false && na.detail.ky.here === true
     && /Kentucky closures to the Newburgh ASC: not available/.test(na.notes.join(' ')), na.notes.join(' '));
  // one Kentucky flap: in the clinic, Kentucky's office amounts, 650.00 + half of 590.00 = 945.00; sent to Newburgh, the Mohs
  // at Kentucky's office amount (590.00) outranks the flap at Indiana's facility amount (480.00): 590.00 + 240.00 = 830.00;
  // the ASC's fee 1,000.00
  const e = kyPayload(), on = stepIn(e, { ky_share: 1, own: 1 }), off = stepIn(e, { own: 1 });
  const n = 5 * per, A1 = 65000 + 29500, B1 = 59000 + 24000;
  ok('B_ky_flap_priced', near(on.collections - off.collections, n * (B1 - A1 + 100000) * coll('14040'), 1e-9) && near(on.practiceOnly - off.practiceOnly, n * 100000 * coll('14040'), 1e-9)
     && near(on.ownProduction - off.ownProduction, n * (B1 - A1) * coll('14040'), 1e-9) && near(on.detail.ky.moved, n, 1e-9), `${on.collections - off.collections}`);
  // the cut across the two contractors switched off: each claim on its own (590.00 + 480.00)
  const nc = stepIn(e, { ky_share: 1, own: 1, ky_cross: 0 });
  ok('B_ky_cross_switch', near(nc.collections - on.collections, n * (107000 - B1) * coll('14040'), 1e-9) && /billing must still apply it/.test(nc.notes.join(' '))
     && /billing must apply it/.test(on.notes.join(' ')), `${nc.collections - on.collections}`);
  // flaps and grafts by default; the complex repair (13132 + 13133) only when ticked: clinic 590.00 + 195.00 + 145.00,
  // the ASC 590.00 + 110.00 + 90.00 and its fee 200.00 (the add-on packaged)
  const cx = stepIn(e, { ky_share: 1, own: 1, ky_complex: 1 });
  ok('B_ky_types', near(cx.collections - on.collections, 3 * per * (79000 + 20000 - 93000) * coll('13132'), 1e-9), `${cx.collections - on.collections}`);
  // Medicare Advantage in Kentucky waits for its facility fee, as at Newburgh
  const ma = stepIn(e, { ky_share: 1, own: 1, commercial_pct: 0.9 });
  ok('B_ky_ma_needs_fee', near(ma.detail.ky.moved, 9 * per, 1e-9) && near(on.detail.ky.moved, 5 * per, 1e-9) && on.needs.includes('param.commercial_pct'), JSON.stringify([ma.detail.ky.moved, on.detail.ky.moved]));
  // per closure type, and what the patient pays more: 20% of (830.00 + 1,000.00 - 945.00)
  const flap = on.detail.ky.types.find(t => t.type === 'flap'), comp = on.detail.ky.types.find(t => t.type === 'complex');
  ok('B_ky_per_case', near(flap.owner, B1 + 100000 - A1) && near(flap.nonOwner, B1 - A1) && near(flap.coins, 0.2 * (B1 + 100000 - A1)) && flap.sent && !comp.sent && comp.lowMargin
     && /pays about \$177 more per case/.test(on.notes.join(' ')) && /45 minutes/.test(on.notes.join(' ')), JSON.stringify(flap) + on.notes.join(' '));
  // the Kentucky flaps sent to Newburgh leave the stage what-if and join the later-day ASC what-if (nothing there when the
  // cut is not applied across the contractors: the same-day and later-day amounts are then the same)
  const all = { share: 1, all: 1, full: 0 }, later = { share: 1 };
  const runK = p => LM.run(e, x0, A, { asc: { on: true, params: Object.assign({ own: 1 }, p) }, stage: { on: true, params: all }, asc_later: { on: true, params: later } }, cal);
  const k0 = runK({}), k1 = runK({ ky_share: 1 }), k2 = runK({ ky_share: 1, ky_cross: 0 });
  ok('B_ky_stage_and_later', near(stepOf(k0, 'stage').encountersDay - stepOf(k1, 'stage').encountersDay, 0.5, 1e-9)
     && near(stepOf(k1, 'asc_later').collections - stepOf(k0, 'asc_later').collections, n * (107000 - B1) * coll('14040'), 1e-9)
     && near(stepOf(k2, 'asc_later').collections, stepOf(k0, 'asc_later').collections, 1e-9), JSON.stringify([stepOf(k0, 'stage').encountersDay, stepOf(k1, 'stage').encountersDay]));
  // defaults, the tier, the switch's note and the compliance points
  const P = LM.paramDefaults(S), ks = S.params.filter(q => /^ky_/.test(q[0]));
  ok('B_ky_defaults', P.ky_share === 0 && P.ky_flap === 1 && P.ky_graft === 1 && P.ky_complex === 0 && P.ky_intermediate === 0 && P.ky_cross === 1
     && ks.every(q => q[7] === 'Kentucky closures to the Newburgh ASC') && ks[0][8] === 'medium' && /billing must apply it/.test(ks.find(q => q[0] === 'ky_cross')[1])
     && /may not cover the ASC's cost/.test(ks.find(q => q[0] === 'ky_complex')[1]), '');
  ok('B_ky_compliance', ['14 sec. 20.1', '45 minutes', 'safe harbor', 'capacity', 'CGS', 'WPS'].every(t => S.compliance.includes(t)), '');
  const words = [...ks.map(q => q[1]), ...on.notes, ...nc.notes, ...na.notes];
  ok('B_ky_no_em_dash', words.every(w => !/[—–]/.test(w)), words.find(w => /[—–]/.test(w)));
  ok('B_ky_finite', finiteDeep({ k0: k0.steps, k1: k1.steps, k2: k2.steps }), '');
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

  // ---------------------------------------------------------------- the views drawn, on a small stand-in for the page
  // An element: what is written to it is kept; querySelector gives one element per selector (made on first use);
  // querySelectorAll gives the elements a check put there (inputs, table rows).
  const fakeEl = all => { const kids = {};
    return { innerHTML: '', textContent: '', className: '', value: '', placeholder: '', disabled: false, dataset: {}, style: {}, validity: {}, focus() {},
             querySelector(sel) { return (kids[sel] = kids[sel] || fakeEl(all)); }, querySelectorAll(sel) { return all[sel] || []; } }; };
  const page = all => { const v = fakeEl(all || {}); return { v, body: v.querySelector('#calcBody'), q: sel => v.querySelector('#calcBody').querySelector(sel) }; };
  const text = h => String(h).replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ');
  const amount = s => { const t = text(s).trim(); if (t === '–' || !t) return 0; const m = t.match(/([+−-]?)\$([\d,]+)/); return m ? (m[1] === '−' || m[1] === '-' ? -1 : 1) * +m[2].replace(/,/g, '') : NaN; };
  const input = (labP, value, type) => ({ type: type || 'number', value: String(value), placeholder: '', dataset: { labP }, validity: { badInput: false } });
  ctx.money = c => (c == null ? '–' : (c < 0 ? '-' : '') + '$' + Math.round(Math.abs(c) / 100).toLocaleString());
  ctx.moneyK = c => { const v = Math.round((c || 0) / 100000) * 100000; return v < 0 ? '−' + ctx.money(-v) : ctx.money(v); };
  ctx.calcFmt = { n1: v => (Math.round(v * 10) / 10).toLocaleString() };
  ctx.calcWith = (base, x, over) => Object.assign({}, base, { q: x.q, include_ancillary: x.include_ancillary, include_path: x.include_path, corrected: {} }, over || {});
  // the lab on the ASC payload, every practice figure but the ASC's in
  const e = kyPayload(), labOn = async (chosen, Ax, dd) => {
    server.doc = Object.assign({ comp: {}, scenarios: [] }, Ax); server.version = ver(); server.failRead = false;
    cs.A = null; cs.Aok = false;
    Object.assign(cs, { data: dd || e, x: Object.assign({}, x0), cal, view: 'lab', lab: { chosen }, base: Object.assign({}, x0) });
    const pg = page({ '[data-lab-p]': [] });
    await ctx.labDraw(pg.v);
    return pg;
  };
  const AfDoc = LM.clean(Af);

  // 1: a strategy's cost not entered: the costs are marked, nothing is shown after costs, and the need is listed
  let pg = await labOn({ other_surgeon: { on: true } }, AfDoc, d);
  let sum = pg.q('#labSummary').innerHTML;
  ok('page_i1_costs_open', /Costs a year <span class="small sev-medium">\(some not entered\)/.test(sum) && /not shown until every cost is entered/.test(sum)
     && /Not entered yet for the strategies above: what the surgeon costs a year/.test(sum), text(sum));
  pg = await labOn({ other_surgeon: { on: true, params: { cost: 1000 } } }, AfDoc, d);
  sum = pg.q('#labSummary').innerHTML;
  ok('page_i1_costs_known', /<b>Left after costs<\/b>/.test(sum) && !/some not entered/.test(sum), text(sum));
  // ...and in the scenario table
  pg = await labOn({}, Object.assign({}, AfDoc, { scenarios: [{ name: 'No cost', who: d.who.id, x: { year: '2026' }, chosen: { other_surgeon: { on: true } }, saved_at: '2026-07-01' }] }), d);
  const scen = pg.v.querySelector('#labScenTable').innerHTML;
  ok('page_i1_scenario_costs_open', /No cost/.test(scen) && /some not entered/.test(scen) && /data-label="After costs">–</.test(scen.split('No cost')[1]), text(scen));

  // 31: each line of the summary is the difference of the rounded running totals, so the lines add up to the total shown
  // (vendor fees of a few hundred to a few thousand dollars a year, stacked on other steps, would not add up rounded alone)
  const d31 = payload(); d31.strategy.vendor_fees = { 'Plan A': 280000 }; d31.strategy.vendor_fees_months = 12;
  const bad31 = [];
  let lines = [];
  for (const [vs, pts, vis] of [[0.25, 0.01, 0.5], [0.5, 0.02, 1], [0.75, 0.03, 1.5], [1, 0.04, 2], [0.35, 0.05, 2.5], [0.6, 0.06, 3], [0.9, 0.07, 0.5], [0.45, 0.08, 1]]) {
    pg = await labOn({ app_shift: { on: true, params: { visits: vis, fill: 0 } }, vendor_fees: { on: true, params: { share: vs } }, collect: { on: true, params: { points: pts } },
                       b1: { on: true }, add_session: { on: true, params: { days: 0.5 } } }, AfDoc, d31);
    sum = pg.q('#labSummary').innerHTML;
    const rows = sum.split('<tr').map(r => r.split('</td>').map(c => c.replace(/^[\s\S]*<td[^>]*>/, '')));
    const at = rows.findIndex(r => /Collections a year with these strategies/.test(r[0]));
    const total = amount(rows[at][1]);
    lines = rows.slice(1, at).map(r => amount(r[1]));
    if (!(lines.length === 6 && lines.every(isFinite) && lines.reduce((t, v) => t + v, 0) === total)) bad31.push(JSON.stringify([lines, total]));
  }
  ok('page_i31_lines_add_up', !bad31.length, bad31.join(' '));

  // 36: a step that removes visits says so with a minus sign
  const appImpact = pg.q('[data-lab-impact="app_shift"]').innerHTML;
  ok('page_i36_minus_sign', /−\d\.\d\d visits a clinic day/.test(appImpact) && /−\d+ clinician minutes a day/.test(appImpact) && !/-\d/.test(text(appImpact)), text(appImpact));

  // 32: the ASC's cost per case not entered: its cost is marked and no net is worked out
  const Ac = LM.clean(Object.assign({}, Af, { costs: Object.assign({}, Af.costs, { office_per_case_saved: 50 }) }));
  pg = await labOn({ asc: { on: true, params: { to_asc: 1, own: 1 } } }, Ac);
  const ascOpen = pg.q('[data-lab-impact="asc"]').innerHTML;
  pg = await labOn({ asc: { on: true, params: { to_asc: 1, own: 1 } } }, LM.clean(Object.assign({}, Ac, { costs: Object.assign({}, Ac.costs, { asc_per_case: 300 }) })));
  const ascKnown = pg.q('[data-lab-impact="asc"]').innerHTML;
  ok('page_i32_no_net_without_asc_cost', /costs [−+]\$[\d,]+ \(some not entered\)/.test(ascOpen) && !/ net /.test(text(ascOpen)) && / net /.test(text(ascKnown)), text(ascOpen).slice(0, 300));

  // B: the Kentucky table on the ASC card, and 'not available' while the payload has no Kentucky amounts
  pg = await labOn({ asc: { on: true, params: { ky_share: 1, own: 1 } } }, AfDoc);
  const kyCard = pg.q('[data-lab-impact="asc"]').innerHTML;
  pg = await labOn({ asc: { on: true, params: { ky_share: 1, own: 1 } } }, AfDoc, ascPayload());
  const kyNA = pg.q('[data-lab-impact="asc"]').innerHTML;
  // the practice's share not entered and only Kentucky cases moving: both views are shown
  pg = await labOn({ asc: { on: true, params: { ky_share: 1 } } }, AfDoc);
  ok('page_B_ky_views', /class="lab-views"/.test(pg.q('[data-lab-impact="asc"]').innerHTML), '');
  ok('page_B_ky_table', /Kentucky closure, after a Kentucky clinic Mohs/.test(kyCard) && /Patient pays more/.test(kyCard) && /\(not sent\)/.test(kyCard)
     && !/class="lab-views"/.test(kyCard)
     && /Kentucky closures to the Newburgh ASC:<\/b> not available yet/.test(kyNA) && !/Kentucky closure, after/.test(kyNA), text(kyNA).slice(-400));
  const kyHead = ctx.labCard(LM.STRATEGIES.find(s => s.id === 'asc'));
  ok('page_B_ky_section', /<b>Kentucky closures to the Newburgh ASC<\/b> <span class="chip sev-medium">check first<\/span>/.test(kyHead) && kyHead.split('Kentucky closures to the Newburgh ASC</b>').length === 2, '');

  // 26: an emptied box clears the setting: back to not entered when it has no default, else to its default
  server.doc = { comp: {}, scenarios: [] }; server.version = ver(); cs.A = null; cs.Aok = false;
  Object.assign(cs, { data: e, x: Object.assign({}, x0), lab: { chosen: { asc: { on: true, params: { to_asc: 0.5 } }, appeals: { on: true } } } });
  const own = input('asc|own|pct', ''), share = input('appeals|share|pct', '40');
  const pg26 = page({ '[data-lab-p]': [own, share] });
  await ctx.labDraw(pg26.v);
  own.value = '40'; own.onchange({ type: 'change' });
  const typed = cs.lab.chosen.asc.params.own;
  own.value = ''; own.onchange({ type: 'change' });
  const r26 = LM.run(e, cs.x, cs.A, cs.lab.chosen, cal);
  share.value = '100'; share.onchange({ type: 'change' });
  const full = cs.lab.chosen.appeals.params.share;
  share.value = ''; share.onchange({ type: 'change' });
  ok('page_i26_empty_box', typed === 0.4 && cs.lab.chosen.asc.params.own === null && own.placeholder === 'not entered' && stepOf(r26, 'asc').needs.includes('param.own')
     && full === 1 && cs.lab.chosen.appeals.params.share === 0.4 && String(share.value) === '40', JSON.stringify(cs.lab.chosen));

  // 27: two windows change different boxes of one pay plan: each box is kept
  const plan0 = { type: 'base', pct: null, per_wrvu: null, base: 400000, threshold: 1000000, above_pct: 0.3 };
  server.doc = { comp: { '1000000003': Object.assign({}, plan0), '1000000005': { type: 'pct', pct: 0.3 } }, scenarios: [] }; server.version = ver(); server.failRead = false;
  cs.A = null; cs.Aok = false; cs.data = d;
  await ctx.labAssumptions();
  server.doc.comp['1000000003'].base = 450000; server.version = ver();            // another window raises the base
  const box = (c, orig, value) => ({ value: String(value), dataset: { c, orig: String(orig) }, validity: { badInput: false } });
  const row = (id, cells) => ({ dataset: { comp: id, name: 'Clinician ' + id.slice(-1) },
    querySelectorAll: () => cells, querySelector: sel => cells.find(i => sel === `[data-c="${i.dataset.c}"]`) });
  const rowB = row('1000000003', [box('type', 'base', 'base'), box('pct', '', ''), box('per_wrvu', '', ''), box('base', '400000', '400000'), box('threshold', '1000000', '900000'), box('above_pct', '30', '30')]);
  const pg27 = page({ '[data-fig]': [], 'tr[data-comp]': [rowB] });
  await ctx.figuresDraw(pg27.v);
  await pg27.q('#figSave').onclick();
  const p3 = server.doc.comp['1000000003'];
  ok('page_i27_plan_boxes_merged', p3.base === 450000 && p3.threshold === 900000 && p3.above_pct === 0.3 && /were kept/.test(pg27.q('#figMsg').textContent), JSON.stringify(p3));
  // figures typed into a row with no plan: not saved, and the page says to pick a plan first
  const posts27 = server.posts;
  const rowN = row('1000000004', [box('type', '', ''), box('pct', '', '30'), box('per_wrvu', '', ''), box('base', '', ''), box('threshold', '', ''), box('above_pct', '', '')]);
  const pgN = page({ '[data-fig]': [], 'tr[data-comp]': [rowN] });
  await ctx.figuresDraw(pgN.v);
  await pgN.q('#figSave').onclick();
  ok('page_i27_no_plan_kind', server.posts === posts27 && /Pick a kind of plan for Clinician 4/.test(pgN.q('#figMsg').textContent) && !server.doc.comp['1000000004'], pgN.q('#figMsg').textContent);
  // a plan is removed only when its kind is set to no plan here
  const rowD = row('1000000005', [box('type', 'pct', ''), box('pct', '30', '30'), box('per_wrvu', '', ''), box('base', '', ''), box('threshold', '', ''), box('above_pct', '', '')]);
  const pgD = page({ '[data-fig]': [], 'tr[data-comp]': [rowD] });
  await ctx.figuresDraw(pgD.v);
  await pgD.q('#figSave').onclick();
  ok('page_i27_plan_removed', !server.doc.comp['1000000005'] && server.doc.comp['1000000003'].base === 450000, JSON.stringify(server.doc.comp));

  // 3: the pay plans' text says what own production holds
  const figText = pgD.v.querySelector('#calcBody').innerHTML;
  ok('page_i3_own_production_text', /Slides they read \(pathology billed under their number\), staff services such as light treatments and injections, and drugs are not counted in it/.test(figText)
     && !/slides they read themselves included/.test(figText), '');

  // 29: a save whose fresh read fails locks the Save button and the boxes
  server.doc.comp['1000000003'].base = 460000; server.version = ver(); server.failRead = true;
  const fig = { value: '12', dataset: { fig: 'costs.per_encounter', orig: '', label: 'Each patient seen' }, validity: { badInput: false }, disabled: false };
  const pg29 = page({ '[data-fig]': [fig], 'tr[data-comp]': [], '[data-fig], [data-c]': [fig] });
  cs.Aok = true;
  await ctx.figuresDraw(pg29.v);
  await pg29.q('#figSave').onclick();
  ok('page_i29_save_locked', pg29.q('#figSave').disabled === true && fig.disabled === true && /could not be read/.test(pg29.q('#figMsg').textContent), pg29.q('#figMsg').textContent);

  // 29: the provider sheet after a failed read: the read problem, no 'no pay plan', no pay row and no goal
  // (the figures read before, with this clinician's plan, are still in memory)
  server.doc.comp['1000000001'] = { type: 'pct', pct: 0.35 };
  Object.assign(cs, { data: d, x: Object.assign({}, x0), base: Object.assign({}, x0), sheetTarget: '1m', Aok: false, A: LM.filled({ comp: { '1000000001': { type: 'pct', pct: 0.35 } } }) });
  const pgS = page();
  await ctx.sheetDraw(pgS.v);
  const sheetTop = pgS.v.querySelector('#calcBody').innerHTML, sheetBody = pgS.q('#sheetBody').innerHTML;
  ok('page_i29_sheet_read_failed', /could not be read/.test(sheetTop) && !/No pay plan entered/.test(sheetTop) && !/Pay under your plan/.test(sheetBody) && !/To be paid/.test(sheetBody), text(sheetTop).slice(0, 300));

  // 10, 28: the sheet never offers more clinic days or weeks than a schedule holds; within the limits it does
  server.failRead = false; cs.Aok = false; cs.A = null;
  const own0 = M.evaluate(d, Object.assign({}, x0, { include_ancillary: false, include_path: false }), cal).collections;
  const sheetFor = async target => { cs.sheetTarget = target; const p = page(); await ctx.sheetDraw(p.v); return p.q('#sheetBody').innerHTML; };
  const big = await sheetFor(String(Math.round(own0 * 0.35 * 3 / 100))), fits = await sheetFor(String(Math.round(own0 * 0.35 * 1.05 / 100)));
  ok('page_i28_sheet_caps', /Not enough on its own: more clinic days \(it would take more than 6 a week\); more weeks of clinic \(it would take more than 52 a year\)/.test(big)
     && !/more clinic days a week</.test(big) && !/more weeks of clinic a year</.test(big) && /more clinic days a week</.test(fits) && /more weeks of clinic a year</.test(fits)
     && !/Not enough on its own/.test(fits), text(big).slice(-400));
  ok('page_i3_sheet_text', /Slides you read \(pathology billed under your number\), staff services such as light treatments and injections, and drugs are not counted here/.test(fits), '');
}

pageChecks().catch(e => ok('page_checks_ran', false, e.stack)).then(() => process.stdout.write(JSON.stringify(out)));
