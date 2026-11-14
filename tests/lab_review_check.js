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
