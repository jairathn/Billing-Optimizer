// RemitWatch's UI checks (docs/DESIGN.md, "Checks"), in a browser, on a running site with invented data (the demo).
// Never run it on a live site: it opens every page. It prints no page text, only element paths, sizes, counts and
// ratios, so its output can go in a report.
//
//   NODE_PATH=$(npm root -g) node plans/tools/ui/checks.js --base http://127.0.0.1:8771 --user U --pass P \
//     [--owner OWNER_PASSWORD] [--pages today,worklist,...] [--sizes 1366x650,1536x730,1400x900,390x844]
//     [--themes light,dark] [--shots DIR] [--json FILE] [--panel] [--quiet]
//
// For each page, size and theme, on the first screen (what shows before any scrolling):
//   overflow   sideways scroll of the page, px (at most 1)
//   errors     script errors and console errors (none)
//   dialogs    browser dialogs (none: alert, confirm and prompt boxes fail the check)
//   sizes      distinct font sizes of visible text (at most 6)
//   accent     elements that carry the accent: text, fill, edge or ring (at most 8)
//   filled     filled (primary) buttons on the screen (at most 1)
//   contrast   text under 4.5:1 against its rendered background (3:1 for large text), with the element's path
// and on the Work pages: the first row's top, the rows wholly on the first screen, and (with --panel) whether any row
// changes height when an item's panel opens. Exit code 1 when a check fails.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const args = {};
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (!a.startsWith('--')) continue;
  const k = a.slice(2), v = process.argv[i + 1];
  if (v === undefined || v.startsWith('--')) args[k] = true; else { args[k] = v; i++; }
}
const base = (args.base || 'http://127.0.0.1:8771').replace(/\/$/, '');
const PAGES = (args.pages || 'today,worklist,denials,bundling,downcoding,contracts,fees,faxes,wins,results,progress,data,more,overview,alerts,explorer,payers,policy,providers,calculator,/upload,/setup').split(',');
const SIZES = (args.sizes || '1366x650,1536x730,1400x900,390x844').split(',').map(s => s.split('x').map(Number));
const THEMES = (args.themes || 'light,dark').split(',');
const WORK = new Set(['worklist', 'denials', 'bundling', 'downcoding', 'contracts']);
const LIMITS = { overflow: 1, sizes: 6, accent: 8, filled: 1 };
const sleep = ms => new Promise(r => setTimeout(r, ms));

// what a screen measures, in the page (no text leaves it: paths and numbers only)
function measure() {
  const vw = innerWidth, vh = innerHeight;
  const root = getComputedStyle(document.documentElement);
  const toRgb = v => { const c = document.createElement('canvas').getContext('2d'); c.fillStyle = '#000'; c.fillStyle = v.trim(); return c.fillStyle; };
  const hex = h => { h = h.replace('#', ''); return [0, 2, 4].map(i => parseInt(h.slice(i, i + 2), 16)); };
  const accent = ['--accent', '--accent-hover', '--accent-active', '--accent-fg', '--accent-soft', '--accent-soft-2', '--accent-soft-fg', '--focus']
    .map(n => root.getPropertyValue(n)).filter(v => v && v.trim()).map(v => toRgb(v)).filter(v => /^#/.test(v)).map(hex);
  const rgbaOf = s => { const m = /rgba?\(([^)]+)\)/.exec(s || ''); if (!m) return null; const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number); return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]; };
  const isAccent = s => { if (!s || s === 'none') return false; const all = [...String(s).matchAll(/rgba?\([^)]+\)/g)].map(m => rgbaOf(m[0])).filter(c => c && c[3] > 0.05);
    return all.some(c => accent.some(a => Math.abs(a[0] - c[0]) + Math.abs(a[1] - c[1]) + Math.abs(a[2] - c[2]) < 6)); };
  const visible = e => { const r = e.getBoundingClientRect(); if (r.width < 1 || r.height < 1) return null;
    if (r.bottom <= 0 || r.top >= vh || r.right <= 0 || r.left >= vw) return null;
    const s = getComputedStyle(e); if (s.visibility === 'hidden' || s.display === 'none' || Number(s.opacity) === 0) return null;
    for (let p = e; p && p !== document.body; p = p.parentElement) { const ps = getComputedStyle(p); if (Number(ps.opacity) === 0 || ps.visibility === 'hidden') return null; }
    return { r, s }; };
  const ownText = e => [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
  const pathOf = e => { const parts = []; for (let p = e; p && p !== document.body && parts.length < 4; p = p.parentElement) {
    const id = p.id ? '#' + p.id : ''; const cls = typeof p.className === 'string' && p.className.trim() ? '.' + p.className.trim().split(/\s+/).slice(0, 2).join('.') : '';
    parts.unshift(p.tagName.toLowerCase() + id + cls); if (p.id) break; } return parts.join(' > '); };
  // the color actually behind an element: background colors up the tree, alpha-composited; a background image is unknown
  const behind = e => { const stack = []; for (let p = e; p; p = p.parentElement) { const s = getComputedStyle(p);
      if (s.backgroundImage && s.backgroundImage !== 'none' && !/gradient\(/.test(s.backgroundImage)) return null;
      const c = rgbaOf(s.backgroundColor); if (c && c[3] > 0) { stack.push(c); if (c[3] >= 1) break; } }
    let out = [255, 255, 255]; const h = getComputedStyle(document.documentElement).backgroundColor; const hc = rgbaOf(h); if (hc && hc[3] >= 1) out = hc.slice(0, 3);
    for (let i = stack.length - 1; i >= 0; i--) { const c = stack[i]; out = out.map((v, k) => c[k] * c[3] + v * (1 - c[3])); }
    return out; };
  const lum = c => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const out = { sizes: new Set(), accent: [], filled: 0, low: [] };
  for (const e of document.querySelectorAll('body *')) {
    if (e.closest('svg') && e.tagName.toLowerCase() !== 'svg') continue;
    const v = visible(e); if (!v) continue;
    const { s } = v, text = ownText(e);
    if (text) out.sizes.add(parseFloat(s.fontSize));
    const why = [];
    if (text && isAccent(s.color)) why.push('text');
    if (isAccent(s.backgroundColor)) why.push('fill');
    if (parseFloat(s.borderTopWidth) > 0 && isAccent(s.borderTopColor) || parseFloat(s.borderLeftWidth) > 0 && isAccent(s.borderLeftColor)) why.push('edge');
    if (isAccent(s.boxShadow) || (s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0 && isAccent(s.outlineColor))) why.push('ring');
    if (e.tagName.toLowerCase() === 'svg' && isAccent(s.color)) why.push('icon');
    if (why.length) out.accent.push(pathOf(e) + ' [' + why.join('+') + ']');
    if ((e.tagName === 'BUTTON' || e.tagName === 'A') && isAccent(s.backgroundColor) && rgbaOf(s.backgroundColor)[3] > 0.5) out.filled++;
    if (text) { const fg = rgbaOf(s.color), bg = behind(e); if (fg && bg) {
        const c = fg.slice(0, 3).map((v, k) => v * fg[3] + bg[k] * (1 - fg[3]));
        const size = parseFloat(s.fontSize), large = size >= 24 || (size >= 18.66 && Number(s.fontWeight) >= 700);
        const r = ratio(c, bg), bar = large ? 3 : 4.5;
        if (r < bar - 0.01) out.low.push(pathOf(e) + ' ' + r.toFixed(2) + ':1'); } }
  }
  const work = {};
  const rows = [...document.querySelectorAll('#view table.wl tbody tr.row')].filter(r => r.getBoundingClientRect().height > 0);
  if (rows.length) {
    const bar = document.querySelector('.tabbar'); const barTop = bar && getComputedStyle(bar).display !== 'none' ? bar.getBoundingClientRect().top : vh;
    work.firstRowTop = Math.round(rows[0].getBoundingClientRect().top + scrollY);
    work.rowsOnScreen = rows.filter(r => { const b = r.getBoundingClientRect(); return b.top >= 0 && b.bottom <= Math.min(vh, barTop); }).length;
    work.rowHeights = rows.slice(0, 12).map(r => Math.round(r.getBoundingClientRect().height));
  }
  return { overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth, sizes: [...out.sizes].sort((a, b) => a - b),
    accent: out.accent, filled: out.filled, low: out.low, work, title: document.title.replace(/[^\x20-\x7e]/g, '?').slice(0, 80) };
}

(async () => {
  const browser = await chromium.launch();
  const results = [], fails = [];
  for (const theme of THEMES) for (const [w, h] of SIZES) {
    const ctx = await browser.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: 1, colorScheme: theme,
      httpCredentials: args.user ? { username: args.user, password: args.pass } : undefined });
    await ctx.addInitScript(t => { try { localStorage.setItem('rw:guide:v1', '1'); localStorage.setItem('rw:theme', t); } catch (e) {} }, theme);
    const page = await ctx.newPage();
    let errors = [], dialogs = 0;
    page.on('pageerror', e => errors.push('pageerror'));
    page.on('console', m => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errors.push('console'); });
    page.on('dialog', async d => { dialogs++; await d.dismiss().catch(() => {}); });
    let unlocked = false;
    for (const name of PAGES) {
      errors = []; dialogs = 0;
      const url = name.startsWith('/') ? base + name : base + '/?t=' + Date.now() + '#' + name;
      try {
        if (!name.startsWith('/') && (name === 'providers' || name === 'calculator') && args.owner && !unlocked) {
          // the owner's pages open with the owner's password, typed in the Providers page (a reload forgets it)
          await page.goto(base + '/#providers'); await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
          const box = await page.$('#view input[type=password]');
          if (box) { await box.fill(args.owner); await box.press('Enter'); await sleep(1200); unlocked = true; }
        }
        if (unlocked && !name.startsWith('/')) await page.evaluate(h => { location.hash = h; }, name);
        else await page.goto(url);
        await page.waitForLoadState('networkidle', { timeout: 20000 }).catch(() => {});
        await page.waitForFunction(() => { const v = document.querySelector('#view'); return !v || !/^\s*loading/.test(v.innerText || ''); }, null, { timeout: 15000 }).catch(() => {});
        await sleep(700);
        const m = await page.evaluate(measure);
        const r = { page: name, size: `${w}x${h}`, theme, overflow: m.overflow, errors: errors.length, dialogs, sizes: m.sizes, accent: m.accent.length,
          filled: m.filled, low: m.low.length, work: m.work };
        if (WORK.has(name) && args.panel && m.work.rowHeights && m.work.rowHeights.length) {
          const before = m.work.rowHeights;
          await page.click('#view table.wl tbody tr.row td.day', { timeout: 5000 }).catch(() => {});
          await page.waitForSelector('#drawer.open', { timeout: 8000 }).catch(() => {}); await sleep(600);
          const after = await page.evaluate(() => [...document.querySelectorAll('#view table.wl tbody tr.row')].slice(0, 12).map(x => Math.round(x.getBoundingClientRect().height)));
          r.work.panelRowDelta = Math.max(0, ...before.map((v, i) => Math.abs(v - (after[i] == null ? v : after[i]))));
          await page.keyboard.press('Escape').catch(() => {});
        }
        if (args.shots) { fs.mkdirSync(args.shots, { recursive: true });
          await page.screenshot({ path: path.join(args.shots, `${name.replace(/\//g, '')}_${w}x${h}_${theme}.png`) }); }
        const bad = [];
        if (r.overflow > LIMITS.overflow) bad.push(`overflow ${r.overflow}`);
        if (r.errors) bad.push(`${r.errors} script errors`);
        if (r.dialogs) bad.push(`${r.dialogs} dialogs`);
        if (r.sizes.length > LIMITS.sizes) bad.push(`${r.sizes.length} font sizes (${r.sizes.join(', ')})`);
        if (r.accent > LIMITS.accent) bad.push(`${r.accent} accent elements`);
        if (r.filled > LIMITS.filled) bad.push(`${r.filled} filled buttons`);
        if (r.low) bad.push(`${r.low} low-contrast texts: ${m.low.slice(0, 4).join('; ')}`);
        if (bad.length) fails.push(`${name} ${w}x${h} ${theme}: ${bad.join(' | ')}`);
        if (args.verbose) r.accentPaths = m.accent;
        if (args.verbose) r.lowPaths = m.low;
        results.push(r);
        if (!args.quiet) console.error(`${name.padEnd(12)} ${(w + 'x' + h).padEnd(9)} ${theme.padEnd(5)} sizes ${r.sizes.length} accent ${String(r.accent).padStart(2)} filled ${r.filled} low ${r.low} ovf ${r.overflow} err ${r.errors} dlg ${r.dialogs}${r.work.firstRowTop != null ? ` row1 ${r.work.firstRowTop} rows ${r.work.rowsOnScreen}` : ''}${r.work.panelRowDelta != null ? ` panelDelta ${r.work.panelRowDelta}` : ''}`);
      } catch (e) { fails.push(`${name} ${w}x${h} ${theme}: the check stopped (${String(e.message).split('\n')[0].slice(0, 120)})`); }
    }
    await ctx.close();
  }
  await browser.close();
  const out = { results, fails };
  if (args.json) fs.writeFileSync(args.json, JSON.stringify(out, null, 1));
  console.log(JSON.stringify({ checked: results.length, fails: fails.length }));
  for (const f of fails) console.log('FAIL ' + f);
  process.exit(fails.length ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
