// Checks of the calculator's arithmetic (remitwatch/static/calc_model.js) on made-up payloads. Run by
// tests/test_calculator.py with node; prints one JSON object {name: [ok, detail]}.
const M = require('../remitwatch/static/calc_model.js');
const fs = require('fs');
const out = {};
const ok = (name, cond, detail) => { out[name] = [!!cond, detail == null ? '' : String(detail)]; };
const near = (a, b, tol) => Math.abs(a - b) <= (tol == null ? 1e-6 : tol) * Math.max(1, Math.abs(b));

// a hand-made payload: two payers (Medicare, a commercial plan), office rates in cents
function payload() {
  const rates = (pairs) => Object.fromEntries(Object.entries(pairs).map(([g, v]) => [g, { '2026': v, how: 'own' }]));
  const codes = {
    '17311': { family: 'mohs', lever: 'mohs_first', units: 100, allowed_cents: 0, units_by_year: { '2026': 100 } },
    '17312': { family: 'mohs', lever: 'mohs_stage', units: 50, allowed_cents: 0, units_by_year: { '2026': 50 } },
    '14040': { family: 'repair', lever: 'flap_graft', units: 40, allowed_cents: 0, units_by_year: { '2026': 40 } },
    '13132': { family: 'repair', lever: 'layered', units: 20, allowed_cents: 0, units_by_year: { '2026': 20 } },
    '99213': { family: 'est_visit', lever: 'visit_est', units: 200, allowed_cents: 0, units_by_year: { '2026': 200 } },
    '99214': { family: 'est_visit', lever: 'visit_est', units: 100, allowed_cents: 0, units_by_year: { '2026': 100 } },
    'G2211': { family: 'visit_addon', lever: 'g2211', units: 30, allowed_cents: 0, units_by_year: { '2026': 30 } },
    '11102': { family: 'biopsy', lever: 'biopsy_first', units: 50, allowed_cents: 0, units_by_year: { '2026': 50 } },
    '11103': { family: 'biopsy', lever: 'biopsy_add', units: 25, allowed_cents: 0, units_by_year: { '2026': 25 } },
    '17000': { family: 'destruction_premalignant', lever: 'ak', units: 40, allowed_cents: 0, units_by_year: { '2026': 40 } },
    '17003': { family: 'destruction_premalignant', lever: 'ak', units: 120, allowed_cents: 0, units_by_year: { '2026': 120 } },
    '17004': { family: 'destruction_premalignant', lever: 'ak', units: 10, allowed_cents: 0, units_by_year: { '2026': 10 } },
    '77600': { family: 'radiation', lever: 'other', units: 10, allowed_cents: 0, units_by_year: { '2026': 10 } },
  };
  const d = {
    rate_year: '2026', next_year: '2027', years: ['2026'],
    schedule: { clinic_days: 10, days_per_week: 4, weeks_per_year: 46 },
    levers: {
      mohs: { cases: 10, head_neck_share: 1, stages: 1.5, extra_head_neck: 0.5, extra_trunk: null, blocks: 0 },
      flap_graft: { share: 0.4, mix: { '14040': 1 }, add_ons: {} },
      layered: { mohs_share: 0.2, excision_share: 0.75, mix: { '13132': 1 }, add_ons: {}, weights: [0.21, 0.75] },
      excision: { per_day: 0, mix: {} },
      visits: { per_day: 30, new_share: 0, new_mix: {}, est_mix: { '99213': 2 / 3, '99214': 1 / 3 }, g2211_per_visit: 0.1 },
      biopsy: { lesions: 7.5, per_patient: 1.5, first_mix: { '11102': 1 }, add_mix: { '11103': 1 } },
      ak: { lesions: 0, per_patient: 4, share_15_plus: 0.2 },
      benign: { patients: 0, share_15_plus: 0 },
      other: { codes: { '77600': 1 }, groups: { '77600': 'radiation' } },
    },
    codes,
    mix: { mohs: [['Medicare KY', 0.6], ['Plan A', 0.4]], repair: [['Medicare KY', 0.6], ['Plan A', 0.4]],
           est_visit: [['Medicare KY', 0.5], ['Plan A', 0.5]], visit_addon: [['Medicare KY', 0.5], ['Plan A', 0.5]],
           biopsy: [['Medicare KY', 0.5], ['Plan A', 0.5]], destruction_premalignant: [['Medicare KY', 0.5], ['Plan A', 0.5]],
           radiation: [['Medicare KY', 1]], _overall: [['Medicare KY', 0.5], ['Plan A', 0.5]] },
    rates: {
      '17311': rates({ 'Medicare KY': 60000, 'Plan A': 54000 }), '17312': rates({ 'Medicare KY': 37000, 'Plan A': 37000 }),
      '14040': rates({ 'Medicare KY': 70000, 'Plan A': 63000 }), '13132': rates({ 'Medicare KY': 40000, 'Plan A': 36000 }),
      '99213': rates({ 'Medicare KY': 9000, 'Plan A': 8000 }), '99214': rates({ 'Medicare KY': 12800, 'Plan A': 14000 }),
      'G2211': rates({ 'Medicare KY': 1600, 'Plan A': 1500 }),
      '11102': rates({ 'Medicare KY': 8700, 'Plan A': 7000 }), '11103': rates({ 'Medicare KY': 4500, 'Plan A': 4000 }),
      '17000': rates({ 'Medicare KY': 6100, 'Plan A': 6000 }), '17003': rates({ 'Medicare KY': 580, 'Plan A': 1500 }),
      '17004': rates({ 'Medicare KY': 14900, 'Plan A': 14000 }), '77600': rates({ 'Medicare KY': 40000 }),
    },
    medicare: Object.fromEntries(['17311', '17312', '14040', '13132', '99213', '99214', 'G2211', '11102', '11103', '17000', '17003', '17004', '77600']
      .map(c => [c, { '2026': null, '2027': null }])),
    payers: { 'Medicare KY': { kind: 'medicare', correctable: false }, 'Plan A': { kind: 'commercial', correctable: true } },
    paid_share: { medicare: 0.95, commercial: 0.7 }, paid_share_fallback: 0.8, paid_share_class: {}, paid_share_all: null,
    pathology: { per_lesion_cents: 5000 },
    actual: { allowed_cents: 0, paid_cents: 0 },
    constants: { ak_lesions_15_plus: 20, correctable_kinds: ['commercial', 'medicare_advantage', 'medicaid', 'other'], k_range: [0.25, 2],
                 min_k_units: 25, mppr_share: 0.5, app_medicare_share: 0.85 },
    add_on: ['17312', '11103', '17003', 'G2211'],
    mppr: ['17311', '14040', '13132', '11102', '17000'],
  };
  for (const c of Object.keys(d.medicare)) {
    const m = d.rates[c]['Medicare KY']['2026'];
    d.medicare[c] = { '2026': m, '2027': Math.round(m * 0.95) };
  }
  return d;
}

// 1. the levers as they come give back the units billed, code for code
{
  const d = payload(), x = M.defaults(d), u = M.units(d, x);
  const bad = Object.entries(d.codes).filter(([c, cc]) => !near((u[c] || 0) * d.schedule.clinic_days, cc.units, 1e-9) && c !== '17003' && c !== '17000' && c !== '17004');
  ok('levers_give_back_units', bad.length === 0, JSON.stringify(bad.map(([c, cc]) => [c, cc.units, (u[c] || 0) * 10])));
}

// 2. a flap outranks the Mohs first stage: the first stage is paid at half, the flap in full; a layered closure ranks
//    below it: the closure is halved
{
  const d = payload();
  const x = Object.assign(M.defaults(d), { mohs_cases: 1, mohs_hn: 1, mohs_stages: 1, fg_share: 1, lay_mohs: 0, visits: 0, bx_patients: 0, other_radiation: 0 });
  const a = M.allowedByCode(d, x, '2026', false);
  ok('mohs_with_flap_halves_first_stage', near(a['17311']['Medicare KY'], 0.6 * 30000) && near(a['14040']['Medicare KY'], 0.6 * 70000),
     JSON.stringify([a['17311'], a['14040']]));
  const y = Object.assign({}, x, { fg_share: 0, lay_mohs: 1 });
  const b = M.allowedByCode(d, y, '2026', false);
  ok('mohs_with_layered_halves_closure', near(b['17311']['Medicare KY'], 0.6 * 60000) && near(b['13132']['Medicare KY'], 0.6 * 20000),
     JSON.stringify([b['17311'], b['13132']]));
  // two closures on a case: one is paired with the first stage, the other is a later procedure at half
  const z = Object.assign({}, x, { fg_share: 1, lay_mohs: 1 });
  const c = M.allowedByCode(d, z, '2026', false);
  const pairedFlap = 0.5 * 70000, pairedLay = 0.5 * 20000;     // half the cases pair each closure with the first stage
  ok('two_closures_second_at_half', near(c['14040']['Medicare KY'], 0.6 * (pairedFlap + 0.5 * 35000)) && near(c['13132']['Medicare KY'], 0.6 * (pairedLay + 0.5 * 20000)),
     JSON.stringify([c['14040'], c['13132'], c['17311']]));
  // the add-on stage is never reduced
  const s = Object.assign({}, x, { mohs_stages: 2 });
  ok('extra_stage_full_rate', near(M.allowedByCode(d, s, '2026', false)['17312']['Medicare KY'], 0.6 * 37000), '');
}

// 3. AK coding: 17000 + 17003 for each lesion after the first, 17004 alone at 15 or more
{
  const d = payload();
  const x = Object.assign(M.defaults(d), { ak_patients: 2, ak_per_patient: 5, ak_15: 0 });
  const u = M.units(d, x);
  ok('ak_under_15', near(u['17000'], 2) && near(u['17003'], 8) && !u['17004'], JSON.stringify([u['17000'], u['17003'], u['17004']]));
  const y = Object.assign({}, x, { ak_patients: 2, ak_15: 1 });
  const v = M.units(d, y);
  ok('ak_15_plus', near(v['17004'], 2) && !v['17000'] && !v['17003'], JSON.stringify([v['17000'], v['17003'], v['17004']]));
}

// 4. history: with actual allowed equal to what the model predicts times 0.9, k is 0.9 and the year is 0.9 of the model
{
  const d = payload(), x0 = M.defaults(d);
  const a = M.allowedByCode(d, x0, '2026', false);
  for (const [c, rows] of Object.entries(a)) d.codes[c].allowed_cents = 0.9 * Object.values(rows).reduce((t, v) => t + v, 0) * d.schedule.clinic_days;
  const cal = M.calibrate(d);
  const ks = Object.entries(cal.info).filter(([c, i]) => i.from === 'own').map(([c, i]) => i.k);
  ok('k_recovers_history', ks.length > 5 && ks.every(k => near(k, 0.9, 1e-9)), JSON.stringify(ks));
  const r = M.evaluate(d, Object.assign({}, x0, { year: '2026' }), cal);
  const sumActual = Object.values(d.codes).reduce((t, cc) => t + cc.allowed_cents, 0);
  const got = r.production.allowed + r.ancillary.allowed;
  ok('now_equals_history_scaled', near(got, sumActual * x0.days_per_week * x0.weeks_per_year / d.schedule.clinic_days, 1e-6), got + ' vs ' + sumActual);
}

// 5. a corrected contract is raised to the target share of Medicare on the codes it pays below it, and only those
{
  const d = payload(), x = M.defaults(d);
  const fx = (o) => Object.assign({}, x, { corrected: { 'Plan A': true } }, o);
  // whole contract at one share of Medicare: codes below go up, codes above come down
  ok('contract_mode_rebases', M.price(d, fx({ fix_mode: 'contract' }), '17311', 'Plan A', '2026', true) === 60000
     && M.price(d, fx({ fix_mode: 'contract' }), '99214', 'Plan A', '2026', true) === 12800, '');
  // a floor only raises
  ok('floor_mode_raises_only', M.price(d, fx({ fix_mode: 'floor' }), '17311', 'Plan A', '2026', true) === 60000
     && M.price(d, fx({ fix_mode: 'floor' }), '99214', 'Plan A', '2026', true) === 14000, '');
  // the visit schedule only: E/M to the target, procedures untouched
  ok('visits_mode_em_only', M.price(d, fx({ fix_mode: 'visits' }), '99213', 'Plan A', '2026', true) === 9000
     && M.price(d, fx({ fix_mode: 'visits' }), '17311', 'Plan A', '2026', true) === 54000, '');
  ok('per_payer_target', M.price(d, fx({ fix_mode: 'floor', targets: { 'Plan A': 1.1 } }), '17311', 'Plan A', '2026', true) === 66000, '');
  ok('medicare_not_correctable', M.price(d, Object.assign({}, x, { corrected: { 'Medicare KY': true }, target_pct: 1.5 }), '17311', 'Medicare KY', '2026', true) === 60000, '');
}

// 6. collections: payers' share plus the collected share of the patient's; Medicaid balances count for nothing
{
  const d = payload(); d.payers['State Plan'] = { kind: 'medicaid', correctable: false }; d.paid_share.medicaid = 0.4;
  const x = Object.assign(M.defaults(d), { q: { commercial: 0.5, medicaid: 0.9 } });
  ok('collect_commercial', near(M.collectShare(d, x, 'Plan A', '99213'), 0.7 + 0.3 * 0.5), '');
  ok('collect_medicaid_no_balance', near(M.collectShare(d, x, 'State Plan', '99213'), 0.4), '');
}

// 7. 2027: Medicare moves with its amounts; G2211 becomes 16% of the visit at Medicare and keeps its own share elsewhere
{
  const d = payload(), x = Object.assign(M.defaults(d), { year: '2027', indexed: { 'Plan A': false } });
  ok('plan_a_indexed_follows', near(M.price(d, Object.assign({}, x, { indexed: { 'Plan A': true } }), '17311', 'Plan A', '2027', false), 54000 * 0.95, 1e-3), '');
  ok('medicare_2027_amount', near(M.price(d, x, '17311', 'Medicare KY', '2027', false), 57000), M.price(d, x, '17311', 'Medicare KY', '2027', false));
  ok('commercial_2027_unchanged', M.price(d, x, '17311', 'Plan A', '2027', false) === 54000, '');
  const a = M.allowedByCode(d, Object.assign({}, x, { mohs_cases: 0, bx_patients: 0, other_radiation: 0 }), '2027', false);
  const visit = M.visitPrice(d, x, 'Medicare KY', '2027', false);
  ok('g2211_b1_at_medicare', near(a['G2211']['Medicare KY'], 3 * 0.5 * 0.16 * visit), JSON.stringify(a['G2211']) + ' visit ' + visit);
  ok('g2211_commercial_share_kept', near(a['G2211']['Plan A'], 3 * 0.5 * 1500), JSON.stringify(a['G2211']));
}

// 8. Medicare's 2027 same-day rule on a visit with biopsies: 20% off every line but the highest
{
  const d = payload();
  const x = Object.assign(M.defaults(d), { year: '2027', mohs_cases: 0, visits: 10, bx_patients: 2, bx_per_patient: 1.5, bx_visit_share: 1,
                                           ak_patients: 0, est_high: 0, ma_same_day: false, cut_add_ons: true, other_radiation: 0, days_per_week: 1, weeks_per_year: 1 });
  const cal = { k: {}, info: {} };
  const r = M.evaluate(d, x, cal);
  const e = 9000 * 0.95, b1 = 8700 * 0.95, b2 = 4500 * 0.95 * 0.5;    // 2027 Medicare amounts; half an add-on per patient
  const expected = 0.2 * (e + b1 + b2 - Math.max(e, b1)) * 2 * 0.5;   // 2 biopsied patients a day, half of them Medicare
  ok('same_day_cut_visit_biopsy', near(r.sameDay.allowed, expected, 1e-6), r.sameDay.allowed + ' vs ' + expected);
  const y = Object.assign({}, x, { cut_add_ons: false });
  const expected2 = 0.2 * (e + b1 - Math.max(e, b1)) * 2 * 0.5;
  ok('same_day_add_ons_switch', near(M.evaluate(d, y, cal).sameDay.allowed, expected2, 1e-6), '');
  ok('no_same_day_rule_in_2026', M.evaluate(d, Object.assign({}, x, { year: '2026' }), cal).sameDay.allowed === 0, '');
}

// 9. what one more is worth matches the difference of two years, and the goal seek divides the gap by it
{
  const d = payload(), x = M.defaults(d), cal = M.calibrate(d);
  const m = M.marginal(d, x, cal).find(r => r.key === 'mohs_cases');
  const diff = M.evaluate(d, Object.assign({}, x, { mohs_cases: x.mohs_cases + 1 / x.days_per_week }), cal).totalCollections - M.evaluate(d, x, cal).totalCollections;
  ok('marginal_is_difference', near(m.value, diff, 1e-9), m.value + ' vs ' + diff);
  const base = M.evaluate(d, x, cal).totalCollections;
  const g = M.goalSeek(d, x, base + 3 * m.value, cal).find(r => r.key === 'mohs_cases');
  ok('goal_seek_counts_steps', near(g.needed, 3, 1e-6), g.needed);
}

// 10. radiation is the practice's ancillary revenue: apart from the clinician's production, counted only when asked
{
  const d = payload(), x = M.defaults(d), cal = M.calibrate(d);
  const r = M.evaluate(d, x, cal), r2 = M.evaluate(d, Object.assign({}, x, { include_ancillary: true }), cal);
  ok('ancillary_apart', r.ancillary.allowed > 0 && near(r.totalCollections, r.collections) && near(r2.totalCollections, r.collections + r.ancillaryCollections), '');
  ok('goal_seek_volume_only', M.goalSeek(d, x, 1e9, cal).every(m => m.kind === 'volume'), '');
}

// ------------------------------------------------------------------ fixes after the review (one check each)
// 11. goal seek for a clinician with no Mohs: the gap is on every row and on the list; no Mohs move is offered
{
  const d = payload(); d.tier = 'nonphysician'; d.levers.mohs.cases = 0; delete d.rates['17311']; delete d.rates['17312'];
  const x = M.defaults(d), cal = M.calibrate(d), base = M.evaluate(d, x, cal).totalCollections;
  const up = M.goalSeek(d, x, base + 5e6, cal), down = M.goalSeek(d, x, base - 5e6, cal);
  ok('goal_seek_gap_without_mohs', near(up.gap, 5e6) && up.every(m => near(m.gap, 5e6)) && near(up[0].gap, 5e6) && near(down.gap, -5e6)
     && !up.some(m => m.key === 'mohs_cases') && up.some(m => m.needed > 0), JSON.stringify(up.map(m => [m.key, m.gap, m.needed])));
  ok('no_mohs_moves_without_mohs', !M.marginal(d, x, cal).some(m => M.MOHS_MOVES.includes(m.key))
     && M.marginal(payload(), M.defaults(payload())).some(m => m.key === 'mohs_cases'), '');
  // the same-day rule does not cut Mohs cases that have no price at the payer
  const y = Object.assign({}, x, { year: '2027', mohs_cases: 2, mohs_visit_share: 1, visits: 10, bx_patients: 0, ak_patients: 0 });
  ok('same_day_skips_unpriced_mohs', M.evaluate(d, y, { k: {}, info: {} }).sameDay.allowed === 0, M.evaluate(d, y, { k: {}, info: {} }).sameDay.allowed);
}

// 12. pathology from the biopsies: only with both boxes ticked, and without the reads billed under the clinician's own number
{
  const d = payload(); d.pathology = { per_lesion_cents: 5000, generated_cents: 100000, own_read_cents: 40000, biopsy_lesions: 20 };
  const x = Object.assign(M.defaults(d), { year: '2026' }), cal = { k: {}, info: {} };
  const r = o => M.evaluate(d, Object.assign({}, x, o), cal);
  const off = r({ include_ancillary: true, include_path: false }), on = r({ include_ancillary: true, include_path: true }), none = r({ include_path: true });
  const lesions = x.bx_patients * x.bx_per_patient * off.daysYear;
  const lines = v => Object.values(v.byLine).reduce((t, l) => t + l.collections, 0) - v.sameDay.collections;
  ok('include_path_box', near(off.totalCollections, off.collections + off.ancillary.collections) && near(off.totalCollections, lines(off))
     && near(on.path, 5000 * 0.6 * lesions) && near(on.totalCollections, lines(on) + on.pathCollections) && near(none.totalCollections, none.collections),
     [off.totalCollections, on.totalCollections, on.path].join(' '));
}

// 13. k per code and kind of payer: one plan's denials stay with that plan, and the code's total still comes back
{
  const d = payload(), x0 = M.defaults(d), days = d.schedule.clinic_days;
  const a = M.allowedByCode(d, x0, '2026', false)['99214'];
  const med = a['Medicare KY'] * days, com = a['Plan A'] * days;
  d.codes['99214'].allowed_cents = med + 0.5 * com;
  d.codes['99214'].by_kind = { medicare: { units: 60, allowed_cents: med }, commercial: { units: 40, allowed_cents: 0.5 * com } };
  const cal = M.calibrate(d);
  const e = M.evaluate(d, Object.assign({}, x0, { year: '2026' }), cal);
  const yr = e.daysYear / days;
  ok('k_by_kind', near(cal.kind['99214'].medicare, 1) && near(cal.kind['99214'].commercial, 0.5) && near(M.kAt(d, cal, '99214', 'Plan A'), 0.5)
     && near(e.byCode['99214'].allowed, (med + 0.5 * com) * yr), JSON.stringify(cal.kind['99214']));
  // a kind with too few units shares the rest of the code's money; the total still comes back
  d.codes['99214'].by_kind = { medicare: { units: 90, allowed_cents: med }, commercial: { units: 10, allowed_cents: 0.5 * com } };
  const cal2 = M.calibrate(d), e2 = M.evaluate(d, Object.assign({}, x0, { year: '2026' }), cal2);
  ok('k_by_kind_rest', near(cal2.kind['99214']._rest, 0.5) && near(e2.byCode['99214'].allowed, (med + 0.5 * com) * yr), JSON.stringify(cal2.kind['99214']));
}

// 14. the payers' share of a code no payer prices has no patient balance in it
{
  const d = payload();
  ok('payers_share_flat', near(M.payersShare(d, '_flat', '77600'), d.paid_share_fallback) && near(M.payersShare(d, 'Plan A', '99213'), 0.7), M.payersShare(d, '_flat', '77600'));
}

// 15. window months in the year CMS calls next are priced at that year's amounts, not left out of k
{
  const d = payload(); d.years = ['2026', '2027'];
  for (const cc of Object.values(d.codes)) cc.units_by_year = { '2026': cc.units / 2, '2027': cc.units / 2 };
  const x0 = M.defaults(d);
  const a6 = M.allowedByCode(d, x0, '2026', false), a7 = M.allowedByCode(d, x0, '2027', false);
  const tot = (a, c) => Object.values(a[c] || {}).reduce((t, v) => t + v, 0);
  for (const [c, cc] of Object.entries(d.codes)) cc.allowed_cents = 0.9 * 0.5 * (tot(a6, c) + tot(a7, c)) * d.schedule.clinic_days;
  const ks = Object.values(M.calibrate(d).info).filter(i => i.from === 'own').map(i => i.k);
  ok('calibrate_next_year_months', ks.length > 5 && ks.every(k => near(k, 0.9, 1e-9)), JSON.stringify(ks));
}

// 16. the page's own helpers (calc.js): a typed target, slider ranges, rounded rows, what carries to another clinician
const page = (() => {
  const src = fs.readFileSync(require('path').join(__dirname, '../remitwatch/static/calc.js'), 'utf8');
  const grab = head => { const i = src.indexOf(head); let j = src.indexOf('{', i), n = 0;
    for (; j < src.length; j++) { if (src[j] === '{') n++; else if (src[j] === '}' && --n === 0) break; }
    return src.slice(i, j + 1) + (head.startsWith('const') ? ';' : ''); };
  const body = ['function calcMoney(', 'const CALC_CAP', 'function calcRange(', 'function calcRoundRows(', 'function calcKeep('].map(grab).join('\n');
  const assume = src.slice(src.indexOf('const CALC_ASSUME'), src.indexOf('];', src.indexOf('const CALC_ASSUME')) + 2);
  return new Function(assume + '\n' + body + '\nreturn {calcMoney, calcRange, calcRoundRows, calcKeep, CALC_CAP};')();
})();
{
  const want = { '1.6m': 1.6e6, '1.6M': 1.6e6, '$1,600,000': 1.6e6, '1600k': 1.6e6, '1.5k': 1500, '2m': 2e6, ' 950 k ': 950000, '1600000': 1.6e6 };
  const bad = ['abc', '1,6m', '-5', '1.2.3', '', '1e9', '12,34'];
  ok('target_parser', Object.entries(want).every(([s, v]) => near(page.calcMoney(s), v)) && bad.every(s => !isFinite(page.calcMoney(s))),
     JSON.stringify(Object.keys(want).map(s => [s, page.calcMoney(s)])));
  const cs = { base: { weeks_per_year: 50, days_per_week: 5.8, bx_visit_share: 0.93, fg_share: 1.6, other_rest: 1, visits: 50, ak_per_patient: 12 } };
  const hi = def => page.calcRange(cs, def)[1];
  ok('slider_ranges', hi(['weeks_per_year', '', 20, 52, 1, 'n0']) === 52 && hi(['days_per_week', '', 0, 6, 0.1, 'n1']) === 6
     && hi(['bx_visit_share', '', 0, 1, 0.01, 'pct']) === 1 && hi(['fg_share', '', 0, 1.5, 0.01, 'per100']) === 2
     && hi(['other_rest', '', 0, 3, 0.05, 'pct', 'rest']) === 3 && hi(['visits', '', 0, 60, 0.5, 'n1']) === 75 && hi(['ak_per_patient', '', 1, 14, 0.1, 'n1']) === 14
     && String(hi(['bx_visit_share', '', 0, 1, 0.01, 'pct'])).length < 6, '');
  // a typed figure is held between these bounds; a part-time clinician's own figure stays inside them
  const pt = { base: { weeks_per_year: 19.9 } }, [lo, top] = page.calcRange(pt, ['weeks_per_year', '', 20, 52, 1, 'n0']);
  ok('number_box_bounds', lo <= 19.9 && top === 52, [lo, top].join());
  const vals = [12345678, 9876543, 5550000, -2449999, 449999], tot = vals.reduce((a, b) => a + b, 0), r = page.calcRoundRows(vals, tot);
  ok('rows_add_up', r.reduce((a, b) => a + b, 0) === Math.round(tot / 100000) * 100000 && r.every(v => v % 100000 === 0), JSON.stringify(r));
  const d = payload(), base = M.defaults(d);
  const old = Object.assign({}, base, { year: '2026', visits: 99, q: { commercial: 0.4 }, corrected: { 'Plan A': true, 'Gone Plan': true }, include_ancillary: true });
  const keep = page.calcKeep(old, d, base);
  ok('carry_over_rule', keep.year === '2026' && keep.q.commercial === 0.4 && keep.corrected['Plan A'] && !('Gone Plan' in keep.corrected) && !('visits' in keep)
     && keep.include_ancillary === true, JSON.stringify(keep));
}

// 17. goal seek limited to chosen moves (the provider sheet: patient volume only), or measured on production alone
{
  const d = payload(), x = Object.assign(M.defaults(d), { include_ancillary: true }), cal = M.calibrate(d);
  const pick = ['visits', 'new_visits', 'days_per_week'];
  const a = M.goalSeek(d, x, 1e9, cal, { moves: pick }), b = M.goalSeek(d, x, 1e9, { moves: pick, cal });
  const prod = M.goalSeek(d, x, 1e9, cal, { production: true });
  const ev = M.evaluate(d, x, cal);
  ok('goal_seek_moves_option', a.map(m => m.key).join() === pick.join() && b.map(m => m.key).join() === pick.join()
     && near(prod.base, ev.collections) && near(M.goalSeek(d, x, 1e9, cal).base, ev.totalCollections), a.map(m => m.key).join());
  // one more new-patient visit: one more visit, all of it new
  const m = M.marginal(d, x, cal).find(r => r.key === 'new_visits');
  const y = Object.assign({}, x, { visits: x.visits + m.step, new_share: (x.visits * x.new_share + m.step) / (x.visits + m.step) });
  ok('new_patient_move', near(m.value, M.evaluate(d, y, cal).totalCollections - ev.totalCollections, 1e-9), m.value);
}

// 18. 2027: one switch for Medicare Advantage plans that move with Medicare (B1 and the same-day rule together), and the
//     modifier-57 sensitivity when the payload carries the share
{
  const d = payload();
  d.payers['Plan M'] = { kind: 'medicare_advantage', correctable: true, indexed: true };
  for (const fam of ['mohs', 'repair', 'est_visit', 'visit_addon', 'biopsy']) d.mix[fam] = [['Medicare KY', 0.4], ['Plan A', 0.3], ['Plan M', 0.3]];
  for (const c of Object.keys(d.rates)) d.rates[c]['Plan M'] = { '2026': d.rates[c]['Medicare KY']['2026'], how: 'own' };
  const x = Object.assign(M.defaults(d), { year: '2027', visits: 10, bx_patients: 2, ak_patients: 0, other_radiation: 0 });
  const cal = { k: {}, info: {} };
  const on = Object.assign({}, x, { ma_same_day: true }), off = Object.assign({}, x, { ma_same_day: false });
  const g = (xx) => M.allowedByCode(d, xx, '2027', false)['G2211']['Plan M'];
  const cutM = xx => { const e = M.evaluate(d, xx, cal).sameDay.allowed; const y = Object.assign({}, xx, { indexed: { 'Plan M': false } }); return e - M.evaluate(d, y, cal).sameDay.allowed; };
  const n = M.units(d, x)['G2211'];      // G2211 units a day; Plan M has 30% of them
  ok('ma_one_switch', x.ma_same_day === true && near(g(off), n * 0.3 * M.price(d, off, 'G2211', 'Plan M', '2027', false))
     && near(g(on), n * 0.3 * M.B1_SHARE * M.visitPrice(d, on, 'Plan M', '2027', false)) && cutM(on) > 0 && near(cutM(off), 0), [g(on), g(off), cutM(on), cutM(off)].join(' '));
  const base57 = M.evaluate(d, Object.assign({}, x, { mohs_57: true, mohs_visit_share: 0 }), cal).sameDay.allowed;
  d.strategy = { mohs_visit57_share: 0.5 };
  const with57 = M.evaluate(d, Object.assign({}, x, { mohs_57: true, mohs_visit_share: 0 }), cal).sameDay.allowed;
  const without = M.evaluate(d, Object.assign({}, x, { mohs_57: false, mohs_visit_share: 0 }), cal).sameDay.allowed;
  ok('mohs_57_sensitivity', with57 > without && near(without, base57) && M.visit57Share(d) === 0.5, [base57, without, with57].join(' '));
  ok('q_defaults_floor', M.Q_DEFAULT.commercial === 0.59 && M.Q_DEFAULT.medicare_advantage === 0.54 && M.defaults(d).q.commercial === 0.59, '');
  // a code's own payer mix, where the payload has it, comes before its service line's
  const e = payload(); e.mix_code = { '99214': [['Plan A', 1]] };
  const a = M.allowedByCode(e, M.defaults(e), '2026', false)['99214'];
  ok('code_mix_used', a['Plan A'] > 0 && !a['Medicare KY'], JSON.stringify(a));
}

// ------------------------------------------------------------------ the strategy lab (lab_model.js)
const LM = require('../remitwatch/static/lab_model.js');
{
  const d = payload(); d.levers.flap_graft.mix = { '15260': 0.5, '14040': 0.5 };
  d.rates['15260'] = { 'Medicare KY': { '2026': 90000, how: 'own' }, 'Plan A': { '2026': 81000, how: 'own' } };
  d.medicare['15260'] = { '2026': 90000, '2027': 85500 }; d.codes['15260'] = { family: 'repair', lever: 'flap_graft', units: 20, allowed_cents: 0, units_by_year: { '2026': 20 } };
  d.medicare_fac = Object.fromEntries(Object.entries(d.medicare).map(([c, v]) => [c, { '2026': Math.round(v['2026'] * 0.45), '2027': Math.round(v['2027'] * 0.45) }]));
  d.asc = { '2026': { '17311': ['P2', true, { newburgh: 40000 }], '14040': ['A2', true, { newburgh: 110000 }], '15260': ['A2', true, { newburgh: 110000 }] } };
  d.work_rvu = { '2026': { '17311': 6.05, '99213': 1.3 } };
  d.mppr.push('15260');
  d.strategy = { open_findings: { denial: { n: 3, cents: 100000 } }, vendor_fees: { 'Plan A': 6000 } };
  const cal = { k: {}, info: {} };
  const x = Object.assign(M.defaults(d), { year: '2026', mohs_cases: 1, mohs_hn: 1, mohs_stages: 1, fg_share: 1, lay_mohs: 0, visits: 0, bx_patients: 0,
                                           other_radiation: 0, days_per_week: 1, weeks_per_year: 1 });
  const A = LM.blankAssumptions();
  // staging every bigger closure (the graft, not 14040): the first stage on those cases is paid in full
  const r = LM.run(d, x, A, { stage: { on: true, params: { share: 1 } } }, cal);
  const s = r.steps[0];
  const gain = 0.5 * (0.6 * 60000 + 0.4 * 54000) * 0.5;         // half the cases are grafts; their first stage was halved
  ok('lab_stage_gain', near(s.collections, gain * 1, 1e-6) || near(s.collections / M.collectShare(d, x, 'Medicare KY', '17311'), gain, 0.05), s.collections + ' vs ' + gain);
  ok('lab_stage_visits', near(s.encountersDay, 0.5, 1e-9), s.encountersDay);
  ok('lab_needs_named', s.needs.includes('minutes.turnover') && s.needs.includes('costs.per_encounter'), s.needs.join());
  // with the figures entered, the extra visit costs and takes minutes
  A.minutes.turnover = 10; A.costs.per_encounter = 20;
  const r2 = LM.run(d, x, A, { stage: { on: true, params: { share: 1 } } }, cal);
  ok('lab_stage_costs', near(r2.steps[0].cost, 0.5 * 20 * 100, 1e-9) && near(r2.steps[0].minutesDay, 5, 1e-9) && r2.steps[0].needs.length === 0, JSON.stringify(r2.steps[0]));
  // the ASC: facility payment added, professional fee at the facility amounts, both at the multiple-procedure rules
  const r3 = LM.run(d, x, A, { asc: { on: true, params: { cases: 1, own: 1, commercial_pct: 1 } } }, cal);
  const o = LM.caseValue(d, x, cal, 'Medicare KY', '17311', '14040', '2026', 'office', 'newburgh');
  const z = LM.caseValue(d, x, cal, 'Medicare KY', '17311', '14040', '2026', 'asc', 'newburgh');
  ok('lab_asc_office_case', near(o.pro, 70000 + 0.5 * 60000), o.pro);
  ok('lab_asc_asc_case', near(z.pro, 0.45 * 70000 + 0.5 * 0.45 * 60000, 1e-3) && near(z.facility, 110000 + 0.5 * 40000), JSON.stringify(z));
  ok('lab_asc_positive', r3.steps[0].collections > 0, r3.steps[0].collections);
  // appeals are one-time cash; vendor fees a year (12 months of window here), practice-wide
  const r4 = LM.run(d, x, A, { appeals: { on: true, params: { share: 0.5 } }, vendor_fees: { on: true, params: { share: 1 } } }, cal);
  ok('lab_appeals_one_time', near(r4.steps[0].oneTime, 50000) && near(r4.steps[0].collections, 0), JSON.stringify(r4.steps[0]));
  // pay plans and their inverse
  ok('lab_pay_pct', near(LM.pay({ type: 'pct', pct: 0.35 }, 100000000, 0), 35000000), '');
  ok('lab_pay_base', near(LM.pay({ type: 'base', base: 300000, threshold: 1000000, above_pct: 0.4 }, 150000000, 0), 30000000 + 0.4 * 50000000), '');
  ok('lab_pay_wrvu', near(LM.pay({ type: 'wrvu', per_wrvu: 60 }, 0, 1000), 6000000), '');
  ok('lab_collections_for', near(LM.collectionsFor({ type: 'base', base: 300000, threshold: 1000000, above_pct: 0.4 }, 50000000), 100000000 + 20000000 / 0.4), '');
  // a 2027-only strategy is skipped, said so, on 2026 rates
  const r5 = LM.run(d, x, A, { biopsy_return: { on: true } }, cal);
  ok('lab_2027_only', r5.steps[0].skipped, '');
}

process.stdout.write(JSON.stringify(out));
