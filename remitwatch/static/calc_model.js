/* The collections calculator's arithmetic. Pure functions on the payload of /api/activity/calculator
   (remitwatch/calculator.py) and the page's inputs; runs in the browser and, for the tests, in node.

   A schedule (Mohs cases, stages, closures, visits, biopsies, frozen lesions...) is turned into billed units per clinic
   day with the clinician's own code mix inside each lever. Each unit is priced at every payer's usual rate in the payer
   mix of its service line. Where two procedures share a day and both carry Medicare's multiple-procedure indicator, the
   lower one is paid at half (a Mohs first stage beside a flap, an excision beside a layered closure): that pairing is
   modelled case by case, because it moves when the mix of closures moves. Everything else a usual rate does not see
   (denials that stayed denied, later procedures of a day, units, underpayments) is carried by k, per code and, where the
   payload splits a code by kind of payer, per kind: what was allowed in the window over what this model says the same
   schedule was worth at that time's rates. With the levers
   as they come and no contract corrected, the model gives back what was allowed. */
(function (root) {
  'use strict';

  const MEDICARE_KINDS = ['medicare', 'medicare_advantage'];
  const SAME_DAY_CUT = 0.20;           // CY 2027 final rule: 20% off every service but the highest, E/M + 0-day global, same day
  const B1_SHARE = 0.16;               // CY 2027: G2211 replaced by modifier B1, 16% of the visit's RVUs
  const OTHER_INPUT = { radiation: 'other_radiation', skin: 'other_skin', path: 'other_path', drug: 'other_drug', nurse: 'other_nurse', rest: 'other_rest' };
  const ANCILLARY = ['radiation', 'skin', 'path', 'drug', 'nurse'];    // the practice's, not the clinician's own production
  const EM = ['99202', '99203', '99204', '99205', '99211', '99212', '99213', '99214', '99215'];
  // patient balances collected, by kind of payer: assumptions until the practice's patient payments are loaded. Early
  // ledger exports showed about 59% of commercial and 54% of Medicare Advantage balances paid within 2-5 weeks: a floor,
  // used as those two defaults. The Medicare, supplement and other defaults are placeholders, not measurements: by then
  // only about 6% of Medicare and 4% of other balances had been paid, mostly because statements go out after the second
  // payer has paid. Medicare cost-sharing of qualified Medicare beneficiaries (dual eligibles) may not be billed at all;
  // no class of its own here (a small share of Medicare's patient balances).
  const Q_DEFAULT = { commercial: 0.59, medicare: 0.50, medicare_advantage: 0.54, supplement: 0.50, other: 0.30, medicaid: 0 };

  // ------------------------------------------------------------------ inputs
  function shareOf(mix, codes) { return codes.reduce((t, c) => t + (mix[c] || 0), 0); }

  function defaults(d) {
    const L = d.levers, S = d.schedule || {}, St = d.strategy || {};
    const own = (v, dflt) => (v != null && isFinite(v) ? clamp(v, 0, 1) : dflt);
    return {
      days_per_week: round(S.days_per_week || 4, 2), weeks_per_year: round(S.weeks_per_year || 46, 1),
      mohs_cases: L.mohs.cases, mohs_hn: L.mohs.head_neck_share, mohs_stages: L.mohs.stages,
      fg_share: L.flap_graft.share, lay_mohs: L.layered.mohs_share,
      excisions: L.excision.per_day, lay_exc: L.layered.excision_share,
      visits: L.visits.per_day, new_share: L.visits.new_share,
      est_high: shareOf(L.visits.est_mix, ['99214', '99215']), new_high: shareOf(L.visits.new_mix, ['99204', '99205']),
      bx_patients: L.biopsy.lesions / Math.max(1, L.biopsy.per_patient), bx_per_patient: L.biopsy.per_patient,
      ak_patients: akPatientsFromLesions(d, L.ak.lesions, L.ak.per_patient, L.ak.share_15_plus), ak_per_patient: L.ak.per_patient, ak_15: L.ak.share_15_plus,
      benign: L.benign.patients, benign_15: L.benign.share_15_plus,
      other_radiation: 1, other_skin: 1, other_path: 1, other_drug: 1, other_nurse: 1, other_rest: 1,
      q: Object.assign({}, Q_DEFAULT), include_path: false, include_ancillary: false,
      year: d.next_year || d.rate_year, target_pct: 1.0, targets: {}, fix_mode: 'contract', corrected: {},
      indexed: Object.fromEntries(Object.entries(d.payers || {}).map(([g, p]) => [g, !!p.indexed])),
      // this clinician's own shares on Medicare patient-days where the payload has them (strategy.*), else the practice's
      bx_visit_share: own(St.bx_visit_share, 0.93),         // biopsied Medicare patients also billed a visit with modifier 25 that day
      mohs_visit_share: own(St.mohs_visit25_share, 0.045),  // Mohs cases with a visit billed with modifier 25 the same day (57,
                                      // decision for major surgery, is left out: the rule is identified on the claim by
                                      // modifier 25; see mohs_57)
      // of those biopsy-and-visit days, the share that also had AKs destroyed (17000 or 17004), where the payload counts
      // it (strategy.bx_ak_share); else the clinician's AK patients per visit (sameDayCut)
      bx_ak_share: own(St.bx_ak_share, null),
      ma_same_day: true,              // Medicare Advantage plans that move with Medicare take on its 2027 changes too: modifier B1
                                      // in place of G2211 and the same-day rule (the rule governs Part B; a plan follows it by
                                      // contract). One switch for both, so the plans are not favoured one way or the other.
      mohs_57: false,                 // sensitivity: CMS's edits also catch a visit with modifier 57 beside a 0-day Mohs
      cut_add_ons: true,              // the same-day rule cuts add-on codes too (the literal reading; CMS has not said)
    };
  }

  function akPatientsFromLesions(d, lesions, n, f) {
    const per = f * d.constants.ak_lesions_15_plus + (1 - f) * Math.max(1, n || 1);
    return per > 0 ? lesions / per : 0;
  }

  function round(x, n) { const f = Math.pow(10, n || 0); return Math.round(x * f) / f; }

  // E/M level mix with the share at the top two levels moved, the proportions inside each half kept
  function shiftLevels(mix, high, highCodes, fallbackHigh, fallbackLow) {
    const h0 = shareOf(mix, highCodes), out = {};
    for (const c of Object.keys(mix)) {
      const isHigh = highCodes.includes(c);
      if (isHigh) out[c] = h0 > 0 ? mix[c] * high / h0 : 0;
      else out[c] = h0 < 1 ? mix[c] * (1 - high) / (1 - h0) : 0;
    }
    if (h0 <= 0 && high > 0) out[fallbackHigh] = (out[fallbackHigh] || 0) + high;
    if (h0 >= 1 && high < 1) out[fallbackLow] = (out[fallbackLow] || 0) + (1 - high);
    return out;
  }

  // ------------------------------------------------------------------ the schedule as billed units per clinic day
  function units(d, x) {
    const L = d.levers, u = {};
    const add = (c, v) => { if (v) u[c] = (u[c] || 0) + v; };
    // Mohs: first stages by site, extra stages keeping each site's share of the average, blocks
    const C = Math.max(0, x.mohs_cases), h = clamp(x.mohs_hn, 0, 1);
    add('17311', C * h); add('17313', C * (1 - h));
    const extra = Math.max(0, x.mohs_stages - 1);
    const eh = L.mohs.extra_head_neck, et = L.mohs.extra_trunk;
    let rh = 1, rt = 1;
    if (eh != null && et != null && (eh + et) > 0) { rh = eh; rt = et; } else if (eh != null && eh > 0) { rh = 1; rt = 1; }
    const norm = h * rh + (1 - h) * rt;
    add('17312', C * h * (norm > 0 ? extra * rh / norm : extra));
    add('17314', C * (1 - h) * (norm > 0 ? extra * rt / norm : extra));
    add('17315', C * (L.mohs.blocks || 0));
    // closures after Mohs: a flap or graft, or a layered closure (they rarely share a case)
    const R = clamp(x.fg_share, 0, 2), Lm = clamp(x.lay_mohs, 0, 2);
    for (const [c, s] of Object.entries(L.flap_graft.mix)) add(c, C * R * s);
    for (const [c, s] of Object.entries(L.flap_graft.add_ons)) add(c, C * R * s);
    const E = Math.max(0, x.excisions), Le = clamp(x.lay_exc, 0, 2);
    for (const [c, s] of Object.entries(L.excision.mix)) add(c, E * s);
    for (const [c, s] of Object.entries(L.layered.mix)) add(c, (C * Lm + E * Le) * s);
    for (const [c, s] of Object.entries(L.layered.add_ons)) add(c, (C * Lm + E * Le) * s);
    // visits, with the share at 99214 and above (99204 and above for new patients) moved
    const V = Math.max(0, x.visits), n = clamp(x.new_share, 0, 1);
    const est = shiftLevels(L.visits.est_mix, clamp(x.est_high, 0, 1), ['99214', '99215'], '99214', '99213');
    const nw = shiftLevels(L.visits.new_mix, clamp(x.new_high, 0, 1), ['99204', '99205'], '99204', '99203');
    for (const [c, s] of Object.entries(est)) add(c, V * (1 - n) * s);
    for (const [c, s] of Object.entries(nw)) add(c, V * n * s);
    add('G2211', V * (L.visits.g2211_per_visit || 0) * (x.g2211_scale == null ? 1 : Math.max(0, x.g2211_scale)));
    // biopsies: one first code per patient, add-on codes for the rest
    const bp = Math.max(1, x.bx_per_patient || 1), P = Math.max(0, x.bx_patients), B = P * bp;
    for (const [c, s] of Object.entries(L.biopsy.first_mix)) add(c, P * s);
    for (const [c, s] of Object.entries(L.biopsy.add_mix)) add(c, (B - P) * s);
    // premalignant lesions: 17000 + 17003 for each lesion after the first, or 17004 alone for 15 or more
    const ak = akPatients(d, x);
    add('17004', ak.p15); add('17000', ak.pSmall); add('17003', ak.pSmall * Math.max(0, ak.n - 1));
    // benign lesions: 17110 once per patient for up to 14, 17111 for 15 or more
    const Bn = Math.max(0, x.benign), b15 = clamp(x.benign_15, 0, 1);
    add('17110', Bn * (1 - b15)); add('17111', Bn * b15);
    for (const [c, v] of Object.entries(L.other.codes)) add(c, v * otherScale(d, x, c));
    return u;
  }

  // the slider of the group a code of 'everything else' belongs to (1 = as billed now)
  function otherScale(d, x, c) {
    const v = x[OTHER_INPUT[(((d.levers || {}).other || {}).groups || {})[c] || 'rest']];
    return v == null ? 1 : Math.max(0, v);
  }

  function akPatients(d, x) {
    const f = clamp(x.ak_15, 0, 1), nSmall = Math.max(1, Math.min(14, x.ak_per_patient || 1));
    const pat = Math.max(0, x.ak_patients);
    return { patients: pat, p15: pat * f, pSmall: pat * (1 - f), n: nSmall,
             lesions: pat * (f * d.constants.ak_lesions_15_plus + (1 - f) * nSmall) };
  }

  function clamp(v, a, b) { v = Number(v); return isFinite(v) ? Math.min(b, Math.max(a, v)) : a; }

  // ------------------------------------------------------------------ prices
  function payerKind(d, g) { return (d.payers[g] || {}).kind || 'other'; }
  function correctable(d, g) { return (d.constants.correctable_kinds || []).includes(payerKind(d, g)); }
  // next year, a payer whose schedule is a share of Medicare's moves with Medicare's amounts; Medicare itself always does
  function isIndexed(d, x, g) {
    const kind = payerKind(d, g);
    if (kind === 'medicare') return true;
    if (x.indexed && x.indexed[g] != null) return !!x.indexed[g];
    return !!(d.payers[g] || {}).indexed;
  }

  // 2027's policy changes (B1 in place of G2211, the same-day rule): Medicare, and the Medicare Advantage plans that move
  // with Medicare while the one switch for them is on
  function followsMedicare(d, x, g) {
    const kind = payerKind(d, g);
    return kind === 'medicare' || (kind === 'medicare_advantage' && x.ma_same_day !== false && isIndexed(d, x, g));
  }
  // Mohs days with a visit E/M-57 beside the case, when the payload carries this clinician's share (calculator.py)
  function visit57Share(d) {
    const L = d.levers || {}, S = d.strategy || {};
    for (const v of [L.mohs_visit57_share, (L.mohs || {}).visit57_share, (L.mohs || {}).mohs_visit57_share, S.mohs_visit57_share]) if (v != null && isFinite(v)) return +v;
    return null;
  }
  // Mohs cases with a visit the 2027 rule cuts: modifier 25 always; with the sensitivity on, the E/M-57 visits too
  function mohsVisitShare(d, x) {
    const s57 = x.mohs_57 ? visit57Share(d) : null;
    return clamp(x.mohs_visit_share, 0, 1) + (s57 != null ? clamp(s57, 0, 1) : 0);
  }

  // the mix of payers a code is billed to, as [[payer, share]]: the code's own where the payload has it (mix_code, codes
  // with enough lines outside the Mohs case), its service line's otherwise
  function mixOf(d, code) {
    const own = (d.mix_code || {})[code];
    if (own && own.length) return own;
    const fam = (d.codes[code] || {}).family;
    return d.mix[fam] || d.mix._overall || [];
  }

  // one unit of a code at one payer, in a year: the payer's usual rate; for 2027, Medicare and the Medicare Advantage
  // plans that follow it move with Medicare's amounts; a corrected contract is raised to the target share of Medicare
  function price(d, x, code, g, year, corrected) {
    const row = (d.rates[code] || {})[g];
    const kind = payerKind(d, g);
    const next = d.next_year && year === d.next_year;
    const base = row ? row[next ? d.rate_year : year] : null;
    if (base == null) return null;
    let p = base;
    const M = d.medicare[code] || {};
    if (next && isIndexed(d, x, g)) {
      if (code === 'G2211' && (kind === 'medicare' || (kind === 'medicare_advantage' && x.ma_same_day !== false))) return null;   // replaced by modifier B1 (see allowedByCode)
      if (M[d.rate_year] && M[year]) p = base * M[year] / M[d.rate_year];
    }
    if (corrected && x.corrected[g] && correctable(d, g) && M[year]) {
      const T = (x.targets && x.targets[g] != null ? x.targets[g] : x.target_pct) * M[year];
      const mode = x.fix_mode || 'contract';
      if (mode === 'floor') p = Math.max(p, T);
      else if (mode === 'visits') { if (EM.includes(code)) p = T; }
      else p = T;                                 // the whole contract at one share of Medicare: some codes go down
    }
    return p;
  }

  // two procedures of a day under the multiple-procedure rule: true when `a` is the one paid in full beside `b`. Payers
  // rank the pair as Medicare does, by Medicare's amounts for the year, not by their own fees (a plan pays a flap in full
  // and halves the Mohs first stage beside it even where its own fee for the flap is the lower); each payer's own price
  // then applies to the line this ranking halves. Where Medicare has no amount for one of them, the payer's own prices.
  function ranksFirst(d, year, a, b, pa, pb) {
    const M = d.medicare || {}, ma = (M[a] || {})[year], mb = (M[b] || {})[year];
    return ma > 0 && mb > 0 ? ma >= mb : pa >= pb;
  }

  // ------------------------------------------------------------------ allowed per clinic day, by code and payer, before k
  function allowedByCode(d, x, year, corrected) {
    const U = units(d, x), L = d.levers, rho = d.constants.mppr_share;
    const mppr = new Set(d.mppr || []);
    const out = {};      // code -> payer -> cents per day
    const put = (c, g, v) => { if (!v) return; (out[c] = out[c] || {})[g] = (out[c][g] || 0) + v; };
    const done = new Set();
    // a code's price at each payer of a mix; payers with no price leave the code to the payers that have one
    const priced = (code, mix) => {
      const rows = [];
      let w = 0;
      for (const [g, s] of mix) { const p = price(d, x, code, g, year, corrected); if (p != null) { rows.push([g, s, p]); w += s; } }
      return w > 0 ? rows.map(([g, s, p]) => [g, s / w, p]) : [];
    };
    const priceAt = (code, g) => { const p = price(d, x, code, g, year, corrected); return p == null ? fallback(code, g) : p; };
    const avgCache = {};
    const fallback = code => {         // mean over the code's own mix: used when this payer has no rate for a paired code
      if (avgCache[code] == null) { const r = priced(code, mixOf(d, code)); avgCache[code] = r.reduce((t, [, s, p]) => t + s * p, 0); }
      return avgCache[code];
    };
    const pairShare = (anchor, other) => mppr.has(anchor) && mppr.has(other);
    // of a pair, the line paid in full is the one Medicare's amounts rank first, at every payer (see ranksFirst)
    const rank = {};
    const full = (a, b, pa, pb) => {
      const key = a + ' ' + b;
      if (!(key in rank)) { const M = d.medicare || {}, ma = (M[a] || {})[year], mb = (M[b] || {})[year]; rank[key] = ma > 0 && mb > 0 ? ma >= mb : null; }
      return rank[key] == null ? ranksFirst(d, year, a, b, pa, pb) : rank[key];
    };

    // Mohs cases, each billed to the payer of its first stage, with its closure paired against the first stage
    const C = Math.max(0, x.mohs_cases), h = clamp(x.mohs_hn, 0, 1);
    const R = clamp(x.fg_share, 0, 2), Lm = clamp(x.lay_mohs, 0, 2);
    // closures per case: up to one is paired with the first stage; any beyond that (two closures on a case) is a later
    // procedure of the day, paid at the reduced share
    const T = R + Lm, a = Math.min(1, T), fgPair = T > 0 ? R / T * a : 0, lmPair = T > 0 ? Lm / T * a : 0;
    const fgExtra = R - fgPair, lmExtra = Lm - lmPair;
    // the strategy lab can move a share of chosen closures to a later day: they are then not paired with the first stage
    const stage = x.stage || null, staged = c => (stage && stage.codes && stage.codes.includes(c) ? clamp(stage.share, 0, 1) : 0);
    const fg = Object.entries(L.flap_graft.mix), lay = Object.entries(L.layered.mix);
    for (const [f, siteShare] of [['17311', h], ['17313', 1 - h]]) {
      const Cs = C * siteShare;
      if (!(Cs > 0)) continue;
      for (const [g, s, pf] of priced(f, mixOf(d, f))) {
        let firstFactor = 1 - a;
        for (const [j, phi] of fg) {
          const pj = priceAt(j, g), paired = pairShare(f, j), st = staged(j), fFirst = full(f, j, pf, pj);
          firstFactor += fgPair * phi * ((1 - st) * (paired && !fFirst ? rho : 1) + st);
          put(j, g, Cs * s * phi * pj * (fgPair * ((1 - st) * (paired && fFirst ? rho : 1) + st) + fgExtra * (mppr.has(j) ? rho : 1)));
        }
        for (const [l, psi] of lay) {
          const pl = priceAt(l, g), paired = pairShare(f, l), fFirst = full(f, l, pf, pl);
          firstFactor += lmPair * psi * (paired && !fFirst ? rho : 1);
          put(l, g, Cs * s * psi * pl * (lmPair * (paired && fFirst ? rho : 1) + lmExtra * (mppr.has(l) ? rho : 1)));
        }
        if (!fg.length) firstFactor += fgPair;     // no closure codes to pair with: the first stage is paid in full
        if (!lay.length) firstFactor += lmPair;
        put(f, g, Cs * s * pf * firstFactor);
        // add-on codes ride on the case at full rate
        for (const [a, per] of Object.entries(L.flap_graft.add_ons)) put(a, g, Cs * s * R * per * priceAt(a, g));
        for (const [a, per] of Object.entries(L.layered.add_ons)) put(a, g, Cs * s * Lm * per * priceAt(a, g));
      }
    }
    // Mohs extra stages and blocks: add-on codes, full rate, billed to the case's payer
    for (const c of ['17312', '17314', '17315']) {
      if (!U[c]) continue;
      for (const [g, s, p] of priced(c, mixOf(d, c === '17314' ? '17313' : '17311'))) put(c, g, U[c] * s * p);
      done.add(c);
    }
    // excisions, each with its layered closure when there is one
    const E = Math.max(0, x.excisions), Le = clamp(x.lay_exc, 0, 2), LePair = Math.min(1, Le), LeExtra = Le - LePair;
    for (const [i, eps] of Object.entries(L.excision.mix)) {
      for (const [g, s, pi] of priced(i, mixOf(d, i))) {
        let f = 1 - LePair;
        for (const [l, psi] of lay) {
          const pl = priceAt(l, g), paired = pairShare(i, l), iFirst = full(i, l, pi, pl);
          f += LePair * psi * (paired && !iFirst ? rho : 1);
          put(l, g, E * eps * s * psi * pl * (LePair * (paired && iFirst ? rho : 1) + LeExtra * (mppr.has(l) ? rho : 1)));
        }
        if (!lay.length) f += LePair;
        put(i, g, E * eps * s * pi * f);
        for (const [a, per] of Object.entries(L.layered.add_ons)) put(a, g, E * eps * s * Le * per * priceAt(a, g));
      }
    }
    const fromLevers = new Set(['17311', '17313', ...fg.map(e => e[0]), ...lay.map(e => e[0]), ...Object.keys(L.flap_graft.add_ons),
                                ...Object.keys(L.layered.add_ons), ...Object.keys(L.excision.mix)]);
    for (const c of fromLevers) done.add(c);
    // closures past what the levers carry (more than two on a case: calculator.py leaves them under 'everything else',
    // under the lever's own code) are later procedures of the day: each payer's rate, the multiple-procedure share where
    // the code takes it, moved by the slider of their group
    for (const [c, v] of Object.entries(L.other.codes || {})) {
      if (!fromLevers.has(c) || !(v > 0)) continue;
      const n = v * otherScale(d, x, c);
      if (n > 0) for (const [g, s, p] of priced(c, mixOf(d, c))) put(c, g, n * s * p * (mppr.has(c) ? rho : 1));
    }
    // 2027: G2211 is gone at Medicare and the plans that follow it; modifier B1 pays 16% of the visit instead. Every
    // other payer keeps its own share of the code (no renormalising Medicare's share onto them).
    const next = d.next_year && year === d.next_year;
    if (next && U.G2211 > 0) {
      for (const [g, s] of mixOf(d, 'G2211')) {
        const kind = payerKind(d, g);
        if (followsMedicare(d, x, g)) put('G2211', g, U.G2211 * s * B1_SHARE * visitPrice(d, x, g, year, corrected));
        else { const p = price(d, x, 'G2211', g, year, corrected); if (p != null) put('G2211', g, U.G2211 * s * p); }
      }
      done.add('G2211');
    }
    // everything else: units at each payer's rate
    for (const [c, n] of Object.entries(U)) {
      if (done.has(c) || !(n > 0)) continue;
      const rows = priced(c, mixOf(d, c));
      if (rows.length) for (const [g, s, p] of rows) put(c, g, n * s * p);
      else {                              // no payer has a rate: what was allowed per unit, as one flat amount
        const cc = d.codes[c] || {};
        if (cc.units > 0) put(c, '_flat', n * cc.allowed_cents / cc.units);
      }
    }
    return out;
  }

  // the average established visit at one payer, at the levels the inputs set
  function visitPrice(d, x, g, year, corrected) {
    const est = shiftLevels(d.levers.visits.est_mix, clamp(x.est_high, 0, 1), ['99214', '99215'], '99214', '99213');
    let t = 0, w = 0;
    for (const [c, s] of Object.entries(est)) { const p = price(d, x, c, g, year, corrected); if (p != null && s > 0) { t += s * p; w += s; } }
    return w ? t / w : 0;
  }

  // ------------------------------------------------------------------ k: what happened over what the rates predict
  // One k per code, and, where the payload splits a code's lines by kind of payer (codes[c].by_kind: {kind: {units,
  // allowed_cents}}, as shares of the code's own totals), one per code and kind for each kind with enough units: one
  // payer's denials (an E/M line with modifier 57 denied by one plan) then stay with that payer. The kinds without enough
  // units share one k for the rest of the code's money, so the code's total still comes back exactly.
  function calibrate(d) {
    const x0 = defaults(d);
    const days = (d.schedule || {}).clinic_days || 0;
    const modeled = {}, modeledKind = {};
    const one = { k: {}, info: {}, kind: {} };
    for (const y of d.years) {
      // months of the year CMS calls next (the window reaches past the newest rates on file) are priced at that year's
      // amounts, as a plan for that year would be, rather than left out of what the model predicts. What was allowed in
      // those months already carries that year's same-day cut: it comes off the codes it falls on here, so evaluate()
      // does not take it a second time.
      const a = allowedByCode(d, x0, y, false);
      const cutBy = d.next_year && y === d.next_year ? sameDayCut(d, x0, one, y, 1).byCode : {};
      for (const [c, byPayer] of Object.entries(a)) {
        const cc = d.codes[c] || {};
        const uy = (cc.units_by_year || {})[y] || 0;
        const share = cc.units > 0 ? uy / cc.units : 0;
        const less = cutBy[c] || {};
        const mk = modeledKind[c] = modeledKind[c] || {};
        for (const [g, v0] of Object.entries(byPayer)) {
          const v = Math.max(0, v0 - (less[g] || 0)), kd = kindOf(d, g);
          modeled[c] = (modeled[c] || 0) + share * v * days;
          mk[kd] = (mk[kd] || 0) + share * v * days;
        }
      }
    }
    const k = {}, info = {};
    const [lo, hi] = d.constants.k_range, minUnits = d.constants.min_k_units;
    const pool = {};
    for (const [c, cc] of Object.entries(d.codes)) {
      if (!(modeled[c] > 0) || !(cc.units > 0)) continue;
      const p = pool[cc.lever] = pool[cc.lever] || { a: 0, m: 0, u: 0 };
      p.a += cc.allowed_cents; p.m += modeled[c]; p.u += cc.units;
    }
    for (const [c, cc] of Object.entries(d.codes)) {
      let v = null, from = null;
      if (cc.units >= minUnits && modeled[c] > 0) { v = cc.allowed_cents / modeled[c]; from = 'own'; }
      else if (pool[cc.lever] && pool[cc.lever].u >= minUnits && pool[cc.lever].m > 0 && cc.lever !== 'other') { v = pool[cc.lever].a / pool[cc.lever].m; from = 'lever'; }
      else if (cc.units > 0 && modeled[c] > 0) { v = cc.allowed_cents / modeled[c]; from = 'few'; }
      if (v == null) { v = 1; from = 'none'; }
      const clamped = v < lo || v > hi;
      k[c] = clamp(v, lo, hi);
      info[c] = { k: k[c], from, clamped, modeled: modeled[c] || 0, actual: cc.allowed_cents };
    }
    const kind = {};
    for (const [c, cc] of Object.entries(d.codes)) {
      const split = cc.by_kind, mk = modeledKind[c], i = info[c];
      if (!split || !mk || !i || i.from !== 'own' || i.clamped) continue;
      let tu = 0, ta = 0;
      for (const v of Object.values(split)) { tu += Math.max(0, +v.units || 0); ta += Math.max(0, +v.allowed_cents || 0); }
      if (!(tu > 0) || !(ta > 0)) continue;
      const own = {};
      let aOwn = 0, mOwn = 0;
      for (const [kd, v] of Object.entries(split)) {
        const u = cc.units * Math.max(0, +v.units || 0) / tu, A = cc.allowed_cents * Math.max(0, +v.allowed_cents || 0) / ta, m = mk[kd] || 0;
        if (u >= minUnits && m > 0) { own[kd] = A / m; aOwn += A; mOwn += m; }
      }
      if (!Object.keys(own).length) continue;
      const mAll = modeled[c], aAll = cc.allowed_cents;
      let rest = null;
      if (mAll - mOwn > 1e-9 * mAll) rest = (aAll - aOwn) / (mAll - mOwn);
      else if (aOwn > 0) for (const kd of Object.keys(own)) own[kd] *= aAll / aOwn;     // money at kinds the mix does not price
      const vals = Object.values(own).concat(rest == null ? [] : [rest]);
      if (vals.some(v => !(v >= lo && v <= hi))) continue;       // a k out of range: the code's own k for every payer
      kind[c] = Object.assign(own, rest == null ? {} : { _rest: rest });
      i.byKind = kind[c];
    }
    // visit codes whose lines the payload splits by the day they were billed (codes[c].by_kind_visit: procedure_day, the
    // visits billed with modifier 25 or 57 on the day of a 0- or 90-day procedure, and other, every other visit; each
    // kind's two parts add up to by_kind[kind]). A plan's denials of the visit beside a Mohs case then stay on those
    // visits: they keep their own k (proc), the visits a scenario adds, moves to another level or corrects take the k of the
    // other visits (other; visitK), and at the visits billed now the two blend back to the kind's k, so history still
    // comes back. pi is the procedure-day share of the kind's units, u0 the code's units a clinic day in the window; mp
    // and up, what the procedure-day visits were worth at the usual rates and how many there were, over the window.
    const visit = {}, cal0 = { k, kind };
    for (const c of EM) {
      const cc = d.codes[c], sp = cc && cc.by_kind_visit, mk = modeledKind[c], split = cc && cc.by_kind;
      if (!sp || !sp.procedure_day || !sp.other || !split || !mk || !(cc.units > 0) || !(days > 0)) continue;
      let tu = 0;
      for (const v of Object.values(split)) tu += Math.max(0, +v.units || 0);
      if (!(tu > 0)) continue;
      const v = { u0: cc.units / days, pi: {}, proc: {}, other: {}, mp: {}, up: {} }, parts = [];
      for (const [kd, m] of Object.entries(mk)) {
        const P = sp.procedure_day[kd] || {}, O = sp.other[kd] || {};
        const pu = Math.max(0, +P.units || 0), ou = Math.max(0, +O.units || 0), pa = Math.max(0, +P.allowed_cents || 0), oa = Math.max(0, +O.allowed_cents || 0);
        if (m > 0 && pu + ou > 0 && pa + oa > 0) parts.push({ kd, m, pa, oa, ou, pu, pi: pu / (pu + ou), kAll: kKind(cal0, c, kd) });
      }
      // the code's other visits pooled over the kinds: the k for a kind with too few other visits of its own
      let num = 0, den = 0;
      for (const q of parts) { num += q.kAll * q.m * q.oa / (q.pa + q.oa); den += (1 - q.pi) * q.m; }
      const pool = den > 0 ? num / den : null, fits = k => k != null && k >= lo && k <= hi;
      for (const q of parts) {
        if (!(q.pi > 0)) continue;                      // no visits beside a procedure at this kind: its k as it is
        let ko = q.pi < 1 && q.ou * cc.units / tu >= minUnits ? q.kAll * q.oa / (q.pa + q.oa) / (1 - q.pi) : pool, kp;
        if (!fits(ko)) ko = pool;
        if (!fits(ko)) continue;                        // the kind's own k for all of its visits
        if (q.pi >= 1) kp = q.kAll;
        else {
          kp = (q.kAll - (1 - q.pi) * ko) / q.pi;
          if (kp > hi) { kp = hi; ko = (q.kAll - q.pi * kp) / (1 - q.pi); }
          if (kp < 0) { kp = 0; ko = q.kAll / (1 - q.pi); }
          if (!fits(ko)) continue;
        }
        v.pi[q.kd] = q.pi; v.proc[q.kd] = kp; v.other[q.kd] = ko;
        v.mp[q.kd] = q.pi * q.m; v.up[q.kd] = q.pu * cc.units / tu;     // the procedure-day visits: modeled cents, units
      }
      if (Object.keys(v.other).length) { visit[c] = v; info[c].visit = v; }
    }
    return { k, info, kind, visit };
  }

  function kindOf(d, g) { return g === '_flat' ? '_flat' : payerKind(d, g); }
  // k for one code at one payer: the code and kind's own where it has one, the rest of the code's otherwise
  function kAt(d, cal, c, g) { return kKind(cal, c, kindOf(d, g)); }
  function kKind(cal, c, kd) {
    const kk = cal && cal.kind && cal.kind[c];
    if (kk) { const v = kk[kd]; if (v != null) return v; if (kk._rest != null) return kk._rest; }
    return cal && cal.k && cal.k[c] != null ? cal.k[c] : 1;
  }
  // k for a visit code at one payer when the schedule bills U units of it a clinic day: the visits billed beside a
  // procedure in the window (pi of the kind's units now) keep their k, every other visit takes the other visits' k
  function visitKAt(d, cal, c, g, U) {
    const v = cal && cal.visit && cal.visit[c], kd = kindOf(d, g);
    if (!v || v.other[kd] == null) return kAt(d, cal, c, g);
    const r = v.u0 > 0 ? Math.max(0, U || 0) / v.u0 : 0, f = r > 0 ? Math.min(1, v.pi[kd] / r) : 1;
    return f * v.proc[kd] + (1 - f) * v.other[kd];
  }

  // k for a visit code at one payer, for the visits a scenario adds, moves to another level or corrects: the k of the
  // code's lines on days without a procedure where the payload splits them (cal.visit), else kAt
  function visitK(d, cal, c, g) {
    const v = cal && cal.visit && cal.visit[c], o = v ? v.other[kindOf(d, g)] : null;
    return o != null ? o : kAt(d, cal, c, g);
  }

  function sum(o) { let t = 0; for (const v of Object.values(o)) t += v; return t; }

  // ------------------------------------------------------------------ the year
  const LINES = [
    ['mohs', 'Mohs (first and extra stages)', c => ['17311', '17312', '17313', '17314', '17315'].includes(c)],
    ['closures', 'Flaps, grafts and layered closures', (c, cc) => ['flap_graft', 'layered'].includes(cc.lever)],
    ['excisions', 'Excisions', (c, cc) => cc.lever === 'excision'],
    ['visits', 'Visits (E/M and G2211)', (c, cc) => ['visit_new', 'visit_est', 'g2211'].includes(cc.lever)],
    ['biopsies', 'Biopsies', (c, cc) => ['biopsy_first', 'biopsy_add'].includes(cc.lever)],
    ['ak', 'Premalignant destruction (AKs)', (c, cc) => cc.lever === 'ak'],
    ['benign', 'Benign destruction', (c, cc) => cc.lever === 'benign'],
    ['other', 'Other procedures (shaves, ED&C, injections...)', (c, cc, d) => !ANCILLARY.includes(otherGroup(d, c))],
    ['radiation', 'Radiation', (c, cc, d) => otherGroup(d, c) === 'radiation'],
    ['skin', 'Skin-substitute products', (c, cc, d) => otherGroup(d, c) === 'skin'],
    ['path', 'Pathology billed under this number', (c, cc, d) => otherGroup(d, c) === 'path'],
    ['drug', 'Drugs', (c, cc, d) => otherGroup(d, c) === 'drug'],
    ['nurse', 'Staff services (light, injections by staff)', (c, cc, d) => otherGroup(d, c) === 'nurse'],
  ];
  const ANCILLARY_LINES = ANCILLARY;
  function otherGroup(d, c) { return ((d.levers.other || {}).groups || {})[c] || null; }

  function lineOf(d, c) {
    const cc = d.codes[c] || { lever: 'other' };
    for (const [key, , test] of LINES) if (test(c, cc, d)) return key;
    return 'other';
  }

  // collections from allowed: payers' share, plus the patient's share times what is collected of patient balances
  // (Medicaid patients owe almost nothing, so none of a Medicaid balance is counted)
  const PAID_CLASS = { new_visit: 'visit', est_visit: 'visit', visit_addon: 'visit', mohs: 'mohs', pathology: 'pathology' };
  // what the payers themselves pay of the allowed amount (second payers included), before any patient balance
  function paidShare(d, g, code) {
    const kind = g === '_flat' ? null : payerKind(d, g);
    const ps = d.paid_share || {};
    let p = kind && ps[kind] != null ? ps[kind] : d.paid_share_fallback;
    if (p == null) p = 0.85;
    // patients owe more of a visit than of a Mohs case: the kind's share, moved by how this class compares with the whole
    const fam = code ? (d.codes[code] || {}).family : null, cls = fam ? (PAID_CLASS[fam] || 'procedure') : null;
    const pc = cls && d.paid_share_class ? d.paid_share_class[cls] : null;
    if (pc != null && d.paid_share_all) p = clamp(p * pc / d.paid_share_all, 0, 1);
    return p;
  }
  function collectShare(d, x, g, code) {
    const kind = g === '_flat' ? null : payerKind(d, g);
    const p = paidShare(d, g, code);
    const qk = x.q || Q_DEFAULT;
    const q = kind === 'medicaid' ? 0 : clamp(qk[kind] != null ? qk[kind] : (Q_DEFAULT[kind] != null ? Q_DEFAULT[kind] : 0.5), 0, 1);
    return p + (1 - p) * q;
  }

  function evaluate(d, x, cal) {
    cal = cal || calibrate(d);
    const year = x.year || d.rate_year;
    const daysYear = Math.max(0, x.days_per_week) * Math.max(0, x.weeks_per_year);
    const a = allowedByCode(d, x, year, true), U = units(d, x);
    const byLine = {}, byPayer = {}, byCode = {};
    const blk = { production: { allowed: 0, collections: 0, payers: 0 }, ancillary: { allowed: 0, collections: 0, payers: 0 } };
    const shares = {};       // the collected and the payers' shares depend on the payer and the code's family only
    for (const [c, rows] of Object.entries(a)) {
      const line = lineOf(d, c), block = ANCILLARY_LINES.includes(line) ? 'ancillary' : 'production', fam = (d.codes[c] || {}).family;
      for (const [g, v] of Object.entries(rows)) {
        const key = g + ' ' + fam, sh = shares[key] = shares[key] || [collectShare(d, x, g, c), payersShare(d, g, c)];
        const al = v * visitKAt(d, cal, c, g, U[c]) * daysYear, share = sh[0], co = al * share;
        const paid = al * sh[1];
        blk[block].allowed += al; blk[block].collections += co; blk[block].payers += paid;
        const L = byLine[line] = byLine[line] || { allowed: 0, collections: 0 };
        L.allowed += al; L.collections += co;
        if (block === 'production') {
          const P = byPayer[g] = byPayer[g] || { allowed: 0, collections: 0 };
          P.allowed += al; P.collections += co;
        }
        const B = byCode[c] = byCode[c] || { allowed: 0, collections: 0 };
        B.allowed += al; B.collections += co;
      }
    }
    // Medicare's 2027 same-day rule, shown as its own line (on the clinician's own production)
    let sameDay = { allowed: 0, collections: 0 };
    if (d.next_year && year === d.next_year) sameDay = sameDayCut(d, x, cal, year, daysYear);
    const patientBalances = blk.production.collections - blk.production.payers;
    blk.production.allowed -= sameDay.allowed; blk.production.collections -= sameDay.collections;
    // the pathology this clinician's biopsies bring the practice: counted only when both boxes ask for it, and without the
    // reads already billed under the clinician's own number (those are in the 'path' line)
    const path = pathPerLesion(d) * Math.max(0, x.bx_patients) * Math.max(1, x.bx_per_patient || 1) * daysYear;
    const pathColl = path * meanCollect(d, x, 'biopsy');
    const withPath = !!x.include_path;
    const anc = blk.ancillary.collections + (withPath ? pathColl : 0);
    const prod = blk.production.collections;
    return {
      allowed: blk.production.allowed + (x.include_ancillary ? blk.ancillary.allowed + (withPath ? path : 0) : 0),
      collections: prod, production: blk.production, ancillary: blk.ancillary,
      patientBalances,
      sameDay, path, pathCollections: pathColl, ancillaryCollections: anc,
      totalCollections: prod + (x.include_ancillary ? anc : 0),
      daysYear, byLine, byPayer, byCode, units: U,
    };
  }

  // allowed per biopsied lesion for the pathology read for this clinician's biopsies, less the part this clinician read and
  // billed under their own number (pathology.own_read_cents; when the payload does not carry it, the 'path' line billed
  // under this number is taken to be reads of their own biopsies first, up to all of the pathology generated)
  function pathPerLesion(d) {
    const P = d.pathology || {}, per = P.per_lesion_cents || 0, gen = P.generated_cents || 0;
    if (!(per > 0)) return 0;
    if (!(gen > 0)) return per;
    let own = P.own_read_cents;
    if (own == null) { own = 0; for (const [c, cc] of Object.entries(d.codes)) if (otherGroup(d, c) === 'path') own += cc.allowed_cents || 0; }
    return per * Math.max(0, 1 - Math.min(gen, Math.max(0, own)) / gen);
  }

  // the payers' share alone, for every kind of payer and for codes no payer prices ('_flat'): no patient balance in it
  function payersShare(d, g, code) { return paidShare(d, g, code); }

  function meanCollect(d, x, family) {
    const mix = d.mix[family] || d.mix._overall || [];
    let t = 0, w = 0;
    for (const [g, s] of mix) { t += s * collectShare(d, x, g, '88305'); w += s; }
    return w ? t / w : 0.85;
  }

  // 20% off every service but the highest when a visit shares the day with a 0-day procedure (biopsies, Mohs first
  // stages), Medicare and the Medicare Advantage plans that follow it. Each kind of day is priced line by line: the
  // highest service keeps its amount, every other line (add-on codes too, while cut_add_ons is on) loses 20%. Also says
  // where the cut falls (byCode: code -> payer -> allowed a year), so calibrate() can take it off months of the window that
  // already carry it.
  function sameDayCut(d, x, cal, year, daysYear) {
    const L = d.levers, rho = d.constants.mppr_share, mppr = new Set(d.mppr || []);
    const follows = g => followsMedicare(d, x, g);
    const addOns = !!x.cut_add_ons;          // add-on lines counted among the lines the rule cuts, or not
    const raw = (c, g) => price(d, x, c, g, year, true);
    const pr = (c, g) => { const p = raw(c, g); return p == null ? null : p * kAt(d, cal, c, g); };
    const byCode = {};
    let cut = 0, cutColl = 0;
    const amount = rows => rows.reduce((t, r) => t + r[1], 0);
    // `n` days a year of one kind at payer g: each line is [[code, allowed]] (the average visit and the average first
    // biopsy are mixes of codes); `services` are the lines that can be the highest (one service each: not add-ons, not
    // the other services of the day taken together)
    const take = (g, lines, services, n, collCode) => {
      if (!(n > 0)) return;
      let top = null;
      for (const l of services) if (l.length && (!top || amount(l) > amount(top))) top = l;
      let c = 0;
      const add = (rows, f) => { for (const [code, v] of rows) { const a = f * v * n; if (!a) continue; const o = byCode[code] = byCode[code] || {}; o[g] = (o[g] || 0) + a; c += a; } };
      for (const l of lines) add(l, SAME_DAY_CUT);
      if (top) add(top, -SAME_DAY_CUT);
      cut += c; cutColl += c * collectShare(d, x, g, collCode);
    };
    // the average established visit at payer g, at the levels set, as lines of its codes; a visit billed beside a
    // procedure is valued with the k of visits on other days where the payload splits it (visitK)
    const visit = g => {
      const est = shiftLevels(L.visits.est_mix, clamp(x.est_high, 0, 1), ['99214', '99215'], '99214', '99213');
      const rows = []; let w = 0;
      for (const [c, s] of Object.entries(est)) { const p = raw(c, g); if (p != null && s > 0) { rows.push([c, s * p * visitK(d, cal, c, g)]); w += s; } }
      return w ? rows.map(([c, v]) => [c, v / w]) : [];
    };
    const line = (c, g, f) => { const p = pr(c, g); return p ? [[c, p * (f == null ? 1 : f)]] : []; };
    // visits with a biopsy; of them, a share with AKs destroyed too: the payload's own count of such days where it has
    // one (moved with the AK patients a visit as the slider moves them), else the clinician's AK patients a visit
    const bp = Math.max(1, x.bx_per_patient || 1), bxPatients = Math.max(0, x.bx_patients);
    const withVisit = Math.min(bxPatients * clamp(x.bx_visit_share, 0, 1), Math.max(0, x.visits));
    const ak = akPatients(d, x), akRate = x.visits > 0 ? ak.patients / x.visits : 0;
    let akW = Math.min(1, akRate);
    if (x.bx_ak_share != null && isFinite(x.bx_ak_share)) {
      const v0 = L.visits.per_day, a0 = v0 > 0 ? akPatientsFromLesions(d, L.ak.lesions, L.ak.per_patient, L.ak.share_15_plus) / v0 : 0;
      akW = clamp(a0 > 0 ? x.bx_ak_share * akRate / a0 : (akRate > 0 ? x.bx_ak_share : 0), 0, 1);
    }
    const f15 = clamp(x.ak_15, 0, 1);
    const other = Math.max(0, +((d.strategy || {}).bx_day_other_cents) || 0);   // other services on those days, where counted
    for (const [g, s] of mixOf(d, '11102')) {
      if (!follows(g)) continue;
      const E = visit(g);
      const B1 = Object.entries(L.biopsy.first_mix).flatMap(([c, w]) => line(c, g, w));
      const B2 = addOns ? Object.entries(L.biopsy.add_mix).flatMap(([c, w]) => line(c, g, w * (bp - 1))) : [];
      const O = other > 0 ? [['_other', other]] : [];
      const A0 = line('17000', g), A3 = addOns ? line('17003', g, Math.max(0, ak.n - 1)) : [], A4 = line('17004', g);
      for (const [w, akLines, akTop] of [[1 - akW, [], []], [akW * (1 - f15), [A0, A3], A0], [akW * f15, [A4], A4]])
        take(g, [E, B1, B2, O, ...akLines], [E, B1, akTop], withVisit * s * daysYear * w, '11102');
    }
    // Mohs cases with a visit the same day: the first stage, the closure paired with it (the lower of the two, by
    // Medicare's ranking, at half), the extra stages
    const C = Math.max(0, x.mohs_cases), h = clamp(x.mohs_hn, 0, 1);
    const R0 = clamp(x.fg_share, 0, 2), L0 = clamp(x.lay_mohs, 0, 2), T = R0 + L0;
    const R = T > 1 ? R0 / T : R0, Lm = T > 1 ? L0 / T : L0;       // the closure paired with the first stage
    const u = units(d, x), share = clamp(mohsVisitShare(d, x), 0, 1);
    for (const [f, site, addc] of [['17311', h, '17312'], ['17313', 1 - h, '17314']]) {
      const cases = C * site * share;
      if (!(cases > 0)) continue;
      const extraPerCase = (u[addc] || 0) / (C * site);
      for (const [g, s] of mixOf(d, f)) {
        if (!follows(g)) continue;
        const pf = raw(f, g);
        if (pf == null) continue;           // no price for the first stage at this payer: no Mohs case is billed to it
        const kf = kAt(d, cal, f, g), E = visit(g), X = addOns ? line(addc, g, extraPerCase) : [];
        let none = 1;
        for (const [sh, mix] of [[R, L.flap_graft.mix], [Lm, L.layered.mix]]) {
          for (const [j, w] of Object.entries(mix)) {
            const p = sh * w, pj = raw(j, g);
            none -= p;
            if (!(p > 0)) continue;
            const paired = pj != null && mppr.has(f) && mppr.has(j), fFirst = ranksFirst(d, year, f, j, pf, pj || 0);
            const F = [[f, pf * kf * (paired && !fFirst ? rho : 1)]];
            const J = pj ? [[j, pj * kAt(d, cal, j, g) * (paired && fFirst ? rho : 1)]] : [];
            take(g, [E, X, F, J], [E, F, J], cases * s * daysYear * p, f);
          }
        }
        const F = [[f, pf * kf]];
        take(g, [E, X, F], [E, F], cases * s * daysYear * Math.max(0, none), f);
      }
    }
    return { allowed: cut, collections: cutColl, byCode };
  }

  // ------------------------------------------------------------------ what one more of each lever is worth, and what a target takes
  const MOVES = [
    // [key, label, step, short name, kind]: 'volume' levers may be planned to a target; 'clinical' ones are what-ifs only
    ['mohs_cases', 'One more Mohs case a week', 'week', 'Mohs cases', 'volume'],
    ['excisions', 'One more excision a week', 'week', 'Excisions', 'volume'],
    ['visits', 'One more visit a week', 'week', 'Visits', 'volume'],
    ['new_visits', 'One more new-patient visit a week', 'week', 'New patients', 'volume'],
    ['bx_patients', 'One more biopsied patient a week', 'week', 'Biopsied patients', 'volume'],
    ['ak_patients', 'One more AK patient a week', 'week', 'AK patients', 'volume'],
    ['benign', 'One more benign-destruction patient a week', 'week', 'Benign-destruction patients', 'volume'],
    ['days_per_week', 'Half a clinic day more a week', 0.5, 'Clinic days', 'volume'],
    ['weeks_per_year', 'One more week of clinic a year', 1, 'Weeks a year', 'volume'],
    ['fg_share', '10 more flaps or grafts per 100 Mohs cases', 0.10, 'Flaps and grafts per 100 Mohs cases', 'clinical'],
    ['mohs_stages', 'A tenth of a stage more per Mohs case', 0.10, 'Stages per Mohs case', 'clinical'],
    ['est_high', '10 more of every 100 established visits at 99214 or above', 0.10, 'Established visits at 99214 or above', 'clinical'],
  ];

  function step(x, key, how) {
    if (how === 'week') return 1 / Math.max(0.5, x.days_per_week);
    if (how === 'week5') return 5 / Math.max(0.5, x.days_per_week);
    return how;
  }

  // a clinician who does Mohs: a physician with Mohs cases in the months read and a price for a first stage at some payer.
  // For anyone else the Mohs moves (more cases, closures per case, stages per case) are not offered.
  const MOHS_MOVES = ['mohs_cases', 'fg_share', 'mohs_stages'];
  function hasMohs(d) {
    if (d.tier === 'nonphysician') return false;
    if (!(((d.levers || {}).mohs || {}).cases > 0)) return false;
    return ['17311', '17313'].some(c => Object.keys((d.rates || {})[c] || {}).length > 0);
  }

  // one step of a move: the lever raised by dx; a new-patient visit is one more visit, all of it new
  function moved(x, key, dx) {
    if (key === 'new_visits') {
      const V = Math.max(0, x.visits), nv = V * clamp(x.new_share, 0, 1);
      return Object.assign({}, x, { visits: V + dx, new_share: V + dx > 0 ? (nv + dx) / (V + dx) : 1 });
    }
    return Object.assign({}, x, { [key]: x[key] + dx });
  }
  // opts (optional): moves, a list of MOVES keys to use; production, true to measure the clinician's own production only
  // (ancillary and the biopsies' pathology left out, as pay on production is)
  function measure(ev, opts) { return opts && opts.production ? ev.collections : ev.totalCollections; }

  function marginal(d, x, cal, opts) {
    cal = cal || calibrate(d);
    const base = measure(evaluate(d, x, cal), opts), mohs = hasMohs(d), only = opts && Array.isArray(opts.moves) ? opts.moves : null;
    return MOVES.filter(([key]) => (mohs || !MOHS_MOVES.includes(key)) && (!only || only.includes(key))).map(([key, label, how, name, kind]) => {
      const dx = step(x, key, how);
      const v = measure(evaluate(d, moved(x, key, dx), cal), opts) - base;
      return { key, label, name, kind, step: dx, value: v };
    });
  }

  // how much of one lever reaches a yearly target, the others held. The gap is the same for every lever and is on every row
  // and on the list itself (res.gap); a lever worth nothing for this clinician (under a dollar a year) gets no count
  // (needed: null). Called goalSeek(d, x, target, cal, opts); an opts object in cal's place is taken as opts (with
  // opts.cal, if given, as the calibration).
  function goalSeek(d, x, target, cal, opts) {
    if (cal && !cal.k && (cal.moves || cal.production != null || cal.cal)) { opts = cal; cal = opts.cal; }
    cal = cal || calibrate(d);
    const base = measure(evaluate(d, x, cal), opts), gap = target - base;
    const res = marginal(d, x, cal, opts).filter(m => m.kind === 'volume').map(m =>
      Object.assign(m, { gap, needed: m.value >= 100 ? (gap > 0 ? stepsTo(d, x, cal, opts, m, target, gap) : gap / m.value) : null }));     // number of steps
    res.gap = gap; res.base = base;
    return res;
  }

  // the number of steps of one move that brings the measure to the target. Collections are linear in most levers, and the
  // count is then the gap over one step's value; next year's same-day rule bends them in visits, biopsied and AK patients
  // (only so many biopsies have a visit beside them, and AKs on those days follow the AK patients a visit), so the count is
  // checked on the model and, where it misses, found between a count that falls short and one that reaches the target
  // (regula falsi). null when no count up to 256 times the first guess reaches it.
  function stepsTo(d, x, cal, opts, m, target, gap) {
    const f = n => measure(evaluate(d, moved(x, m.key, n * m.step), cal), opts) - target;
    const tol = Math.max(100, 1e-4 * gap);
    let lo = 0, flo = -gap, hi = gap / m.value, fhi = f(hi);
    if (Math.abs(fhi) <= tol) return hi;
    for (let i = 0; fhi < 0 && i < 8; i++) { lo = hi; flo = fhi; hi *= 2; fhi = f(hi); }
    if (fhi < 0) return null;
    let side = 0;
    for (let i = 0; i < 12; i++) {
      const n = hi - fhi * (hi - lo) / (fhi - flo), fn = f(n);
      if (Math.abs(fn) <= tol) return n;
      if (fn > 0) { hi = n; fhi = fn; if (side > 0) flo /= 2; side = 1; }
      else { lo = n; flo = fn; if (side < 0) fhi /= 2; side = -1; }
    }
    return hi;              // reaches the target, if by a little more than the tolerance
  }

  const api = { defaults, units, allowedByCode, calibrate, evaluate, marginal, goalSeek, price, shiftLevels, LINES, MOVES, ANCILLARY, Q_DEFAULT, EM, isIndexed, payersShare,
                MEDICARE_KINDS, SAME_DAY_CUT, B1_SHARE, collectShare, payerKind, correctable, mixOf, akPatients, visitPrice,
                kAt, hasMohs, MOHS_MOVES, paidShare, pathPerLesion, followsMedicare, visit57Share, mohsVisitShare,
                sameDayCut, ranksFirst, visitK, visitKAt };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.CalcModel = api;
})(typeof window !== 'undefined' ? window : globalThis);
