// Evaluate the page's own drawerExtras (features.js) with its own esc and money2 (index.html) at a fixed instant.
// Usage: TZ=America/New_York node drawer_eval.js <iso instant> <json cases>   Invented data only.
const fs = require('fs');
const vm = require('vm');
const ROOT = require('path').join(__dirname, '..', '..', 'remitwatch', 'static') + '/';
const html = fs.readFileSync(ROOT + 'index.html', 'utf8');
const feat = fs.readFileSync(ROOT + 'features.js', 'utf8');
const grab = (src, re) => { const m = src.match(re); if (!m) throw new Error('not found: ' + re); return m[0]; };
const helpers = [grab(html, /const money2 = [^\n]*\n/), grab(html, /const esc = [^\n]*\n/)].join('');
const start = feat.indexOf('function drawerExtras(f, r){'), end = feat.indexOf('const REC_LABEL');
const fn = feat.slice(start, end);
const instant = process.argv[2];
const cases = JSON.parse(process.argv[3]);
const RealDate = Date;
class FakeDate extends RealDate { constructor(...a) { if (a.length === 0) super(instant); else super(...a); } static now() { return new RealDate(instant).getTime(); } }
const ctx = { Date: FakeDate, String, JSON, Math, Number, Array, Object, console };
vm.createContext(ctx);
vm.runInContext(helpers.replace(/^const /gm, 'var ') + fn + '\nthis.drawerExtras = drawerExtras;', ctx);
// the drawer shows the notes, then folds the facts and the note builder: all of it is the drawer's text
const whole = x => typeof x === 'string' ? x : [x.notes, x.facts, x.builder].join('');
const out = cases.map(c => { try { return { ok: true, html: whole(ctx.drawerExtras(c.f, c.r || {})) }; } catch (e) { return { ok: false, error: String(e) }; } });
const local = new RealDate(instant);
console.log(JSON.stringify({ local_date: `${local.getFullYear()}-${String(local.getMonth() + 1).padStart(2, '0')}-${String(local.getDate()).padStart(2, '0')}`, out }));
