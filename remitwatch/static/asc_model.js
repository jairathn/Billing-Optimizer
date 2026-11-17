/* The ASC view's arithmetic (asc.js): what the rule year's Medicare rates do to what is paid for the cases the
   practice's surgeons billed at its ASC. Pure functions; runs in the browser and, for the tests, in node.

   The payload (/api/activity/asc_rule, remitwatch.calculator.asc_rule) counts the cases over a window: a Mohs first
   stage with its closure the same day, or an excision with its repair, by the codes billed, where the closure was billed
   (site 'asc': on the ASC's claim, place of service 24), the kind of payer and the state. Each case billed at the ASC is
   priced in the year before and in the rule year with the strategy lab's own arithmetic (LabModel.ascCase):

     the ASC     Medicare's ASC rate for the ASC's claim (Addendum AA, wage-adjusted for the ASC's county; the highest
                 procedure in full and every other one subject to discounting at half; packaged codes nothing; the lower
                 rates of an ASC failing quality reporting when that is ticked)
     surgeon     Medicare's physician amounts (the Mohs in the clinic at its state's office amount, the ASC's lines at the
                 ASC state's facility amount, the 50% rule across the two claims; the 2027 same-day visit rule not counted)

   A plan pays the ASC its share of Medicare's ASC rate. The share starts at what that kind of payer pays the practice's
   surgeons for these procedures against what Medicare pays them (payload kinds[k].ratio), or at the figure entered on
   the lab's ASC card. A payer that follows Medicare's yearly rates moves with them; one that does not is paid in the rule
   year what it was paid the year before. The surgeons' fees are shown for Medicare and Medicare Advantage, which follow
   the physician fee schedule as the calculator takes them; the other plans' are set by their own contracts. */
(function (root) {
  'use strict';
  const LM = (typeof module !== 'undefined' && module.exports) ? require('./lab_model.js') : root.LabModel;

  const KINDS = ['medicare', 'medicare_advantage', 'commercial', 'medicaid', 'other'];
  const LABEL = { medicare: 'Medicare', medicare_advantage: 'Medicare Advantage', commercial: 'Commercial', medicaid: 'Medicaid', other: 'Other payers' };
  const FOLLOWS = { medicare: true, medicare_advantage: true, commercial: true, medicaid: false, other: false };
  const PRO_KINDS = ['medicare', 'medicare_advantage'];
  const TYPES = [['flap', 'Flap'], ['graft', 'Full-thickness graft'], ['complex', 'Complex repair'], ['intermediate', 'Intermediate repair'],
                 ['other', 'Other closure'], ['excision', 'Excision with its repair']];     // 'other': split grafts, skin substitutes, simple repairs
  const MOHS = ['17311', '17312', '17313', '17314', '17315'];
  const SHARE_MAX = 5;

  const kindOf = k => (KINDS.includes(k) ? k : 'other');
  const num = v => (v == null || v === '' || !isFinite(+v)) ? null : +v;
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  // 'code' or 'codexunits', space-separated, as the payload carries a case
  const caseCodes = s => String(s || '').split(/\s+/).filter(Boolean).map(t => { const [c, u] = t.split('x'); return [c, u ? +u : 1]; });

  // The settings to start from. lab: the lab's ASC card's settings where entered there ({own, commercial_pct, ascqr}).
  function defaults(d, lab) {
    const L = lab || {};
    const labShare = num(L.commercial_pct);
    const s = { kinds: {}, own: num(L.own) == null ? null : clamp(+L.own, 0, 1), ascqr: !!L.ascqr };
    for (const k of KINDS) {
      const m = num(((d.kinds || {})[k] || {}).ratio);
      const fromLab = (k === 'medicare_advantage' || k === 'commercial') && labShare != null;
      s.kinds[k] = { share: k === 'medicare' ? 1 : clamp(fromLab ? labShare : (m != null ? m : 1), 0, SHARE_MAX), follows: FOLLOWS[k],
                     from: k === 'medicare' ? 'medicare' : fromLab ? 'lab' : m != null ? 'measured' : 'assumed' };
    }
    return s;
  }

  // Medicare's physician amount for one unit: the ASC's lines at the ASC state's facility amount; the clinic's (the Mohs)
  // at the office amount of the state the case was seen in (Kentucky's for a Kentucky clinic's case)
  function medicarePrice(d, Y, state) {
    const A = d.asc || {}, home = (A.site || {}).state || 'IN';
    return (c, where) => {
      const asc = where === 'asc';
      const tab = asc || state !== 'KY' || home === 'KY' ? A.medicare : A.medicare_ky;
      const r = ((tab || {})[c] || {})[Y];
      const v = r ? r[asc ? 1 : 0] : null;
      return v == null ? null : v;
    };
  }

  // Every case billed at the ASC, a year at the window's pace, priced each year at Medicare's rates. Cases billed partly
  // in the clinic and partly at the ASC the same day are counted apart (split), not priced.
  function book(d, s) {
    const C = d.cases || {}, years = d.years || [], per = +d.per_year || 0;
    const rows = [];
    let split = 0;
    const add = (what, r) => {
      const [codes, site, kind, state, n] = r;
      if (!(n > 0)) return;
      if (site === 'both') { split += n * per; return; }
      if (site !== 'asc') return;
      const stages = what === 'mohs' ? (+r[5] || 0) / n : 0;
      const at = {};
      let ok = true, missing = [];
      for (const Y of years) {
        const v = LM.ascCase(d, Y, what, codes, { stages, cross: true, em: 0, ascqr: !!(s && s.ascqr), price: medicarePrice(d, Y, state) });
        if (!v.ok) { ok = false; missing = missing.concat(v.missing || []); continue; }
        at[Y] = { fee: v.fee, pro: v.B };
      }
      rows.push({ what, codes, kind: kindOf(kind), state, site, n: n * per, type: what === 'mohs' ? LM.caseLabel(codes) : 'excision',
                  ok, missing: [...new Set(missing)].sort(), at });
    };
    for (const r of C.mohs || []) add('mohs', r);
    for (const r of C.excision || []) add('excision', r);
    return { rows, split };
  }

  // What the cases bring in each year (cents a year): the ASC's fees by kind of payer, at the plan's share and following
  // Medicare or not; Medicare's own payment after sequestration and the patients' coinsurance; the practice's share of
  // the ASC; the surgeons' fees for Medicare and Medicare Advantage; per type of case and per code at Medicare's rates.
  function evaluate(d, s) {
    s = s || defaults(d);
    const years = d.years || [], py = d.prior_year;
    const K = (d.constants || {});
    const coins = num(K.coinsurance) == null ? 0.2 : +K.coinsurance, seq = num(K.sequestration) == null ? 0.02 : +K.sequestration;
    const zero = () => Object.fromEntries(years.map(Y => [Y, 0]));
    const B = book(d, s);
    const kinds = {}, types = {}, codes = {};
    for (const k of KINDS) kinds[k] = { n: 0, priced: 0, fee: zero(), pro: PRO_KINDS.includes(k) ? zero() : null };
    let unpriced = 0;
    const missing = new Set();
    for (const r of B.rows) {
      const k = kinds[r.kind];
      const t = types[r.type] = types[r.type] || { n: 0, priced: 0, fee: zero(), pro: zero() };
      k.n += r.n; t.n += r.n;
      if (!r.ok) { unpriced += r.n; r.missing.forEach(c => missing.add(c)); continue; }
      k.priced += r.n; t.priced += r.n;
      const ks = (s.kinds || {})[r.kind] || {};
      const medicare = r.kind === 'medicare';
      const share = medicare ? 1 : clamp(num(ks.share) == null ? 1 : +ks.share, 0, SHARE_MAX);
      const follows = medicare || !!ks.follows;
      const ratio = medicare ? 1 : (num(((d.kinds || {})[r.kind] || {}).ratio) || 1);
      for (const Y of years) {
        k.fee[Y] += r.n * r.at[follows || Y === py ? Y : py].fee * share;
        if (k.pro) k.pro[Y] += r.n * r.at[Y].pro * ratio;
        t.fee[Y] += r.n * r.at[Y].fee;
        t.pro[Y] += r.n * r.at[Y].pro;
      }
      for (const [c, u] of caseCodes(r.codes)) if (!MOHS.includes(c)) codes[c] = (codes[c] || 0) + r.n * u;
    }
    const total = zero(), pro = zero();
    for (const k of KINDS) for (const Y of years) { total[Y] += kinds[k].fee[Y]; if (kinds[k].pro) pro[Y] += kinds[k].pro[Y]; }
    const med = kinds.medicare.fee;
    const by = f => Object.fromEntries(years.map(Y => [Y, f(Y)]));
    return {
      years, kinds, types, codes, total, pro,
      medicare: { paid: by(Y => med[Y] * (1 - coins) * (1 - seq)), coinsurance: by(Y => med[Y] * coins) },
      own: s.own == null ? null : by(Y => total[Y] * s.own),
      n: B.rows.reduce((t, r) => t + r.n, 0), priced: B.rows.reduce((t, r) => t + (r.ok ? r.n : 0), 0),
      split: B.split, unpriced, missing: [...missing].sort(), rows: B.rows,
    };
  }

  const api = { KINDS, LABEL, FOLLOWS, PRO_KINDS, TYPES, defaults, book, evaluate, medicarePrice, caseCodes };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.AscModel = api;
})(typeof window !== 'undefined' ? window : globalThis);
