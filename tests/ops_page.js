// The Data tab's three panels of the parts that run by themselves (static/ops.js), in a browser, at a desk's width and a
// phone's, on a local site over an invented store; and an item's "Why did this change?" (static/features.js).
// Usage: node ops_page.js <port> <user> <password> [screenshot folder]. Prints one JSON line.
const { chromium } = require('playwright');
const [port, user, pass, shots] = process.argv.slice(2);
const base = `http://127.0.0.1:${port}`;

(async () => {
  const browser = await chromium.launch();
  const out = { errors: [], failed: [], widths: {} };
  for (const width of [1400, 390]) {
    const ctx = await browser.newContext({ viewport: { width, height: 1000 }, httpCredentials: { username: user, password: pass } });
    const page = await ctx.newPage();
    page.on('console', m => { if (m.type() === 'error') out.errors.push(`console: ${m.text()}`); });
    page.on('pageerror', e => out.errors.push(`pageerror: ${e.message}`));
    page.on('response', r => { if (r.status() >= 400) out.failed.push(`${r.status()} ${r.url()}`); });
    await page.goto(`${base}/#data`);
    await page.waitForFunction(() => document.querySelector('#opRunning') && document.querySelector('#opLogs') && document.querySelector('#opStore'),
      null, { timeout: 30000 }).catch(() => {});
    const got = await page.evaluate(() => {
      const t = id => { const e = document.getElementById(id); return e ? e.innerText.replace(/\s+/g, ' ').trim() : ''; };
      return { running: t('opRunning'), logs: t('opLogs'), store: t('opStore'),
               overflow: document.documentElement.scrollWidth - window.innerWidth,
               bundle: !!document.querySelector('#opLogs a[href^="/api/ops/diagnostic-bundle"]') };
    });
    if (shots) await page.screenshot({ path: `${shots}/ops-${width}.png`, fullPage: true });
    out.widths[width] = got;
    await ctx.close();
  }
  console.log(JSON.stringify(out));
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
