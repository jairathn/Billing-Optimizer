// The owner's decision 1 on the Vercel site, in a browser: every page says when the site last analysed and that it
// analyses only on a press; the Data tab's "Run the analysis now" starts the run chain and a second press says a run is
// already going; at a phone's width nothing runs off the side.
// Usage: node press_page.js <port> <user> <password> [screenshot folder] [going|failed]. Prints one JSON line. With 'going'
// (a run holds the lock) or 'failed' (the last run stopped before it finished): the pages at a phone's width only, no press.
const { chromium } = require('playwright');
const [port, user, pass, shots, mode] = process.argv.slice(2);
const base = `http://127.0.0.1:${port}`;

(async () => {
  const browser = await chromium.launch();
  const out = { errors: [], failed: [], widths: {} };
  for (const width of (mode ? [390] : [1400, 390])) {
    const ctx = await browser.newContext({ viewport: { width, height: 1000 }, httpCredentials: { username: user, password: pass } });
    const page = await ctx.newPage();
    page.on('pageerror', e => out.errors.push(`pageerror: ${e.message}`));
    page.on('response', r => { if (r.status() >= 500) out.failed.push(`${r.status()} ${r.url()}`); });
    const text = sel => page.evaluate(s => { const e = document.querySelector(s); return e ? e.innerText.replace(/\s+/g, ' ').trim() : ''; }, sel);
    await page.goto(`${base}/#data`);
    await page.waitForSelector('#opAnalysis', { timeout: 30000 }).catch(() => {});
    await page.waitForFunction(() => /analys/.test((document.querySelector('#feedChip') || {}).innerText || ''), null, { timeout: 15000 }).catch(() => {});
    const got = { chip: await text('#feedChip'), panel: await text('#opAnalysis'), feed: await text('#feedPanel'),
                  overflow: await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth) };
    got.button_disabled = await page.evaluate(() => { const b = document.querySelector('#opAnalysis [data-op-press]'); return !!(b && b.disabled); });
    if (width === 1400 && !mode) {
      await page.click('#opAnalysis [data-op-press]');
      await page.waitForFunction(() => { const e = document.querySelector('#opAnalysis [data-op-press-said]'); return e && e.textContent && !/Asking/.test(e.textContent); }, null, { timeout: 20000 }).catch(() => {});
      out.first = await text('#opAnalysis [data-op-press-said]');
      out.second = await page.evaluate(async () => { try { return await api('/api/ops/run', { method: 'POST' }); } catch (e) { return { error: e.message }; } });
    }
    if (shots) await page.screenshot({ path: `${shots}/press-${mode ? mode + '-' : ''}${width}.png`, fullPage: true });
    await page.goto(`${base}/upload`);
    await page.waitForFunction(() => !(document.querySelector('#pressLine') || {}).hidden, null, { timeout: 15000 }).catch(() => {});
    got.upload = await text('#pressLine');
    await page.goto(`${base}/setup`);
    await page.waitForFunction(() => !(document.querySelector('#pressLine') || {}).hidden, null, { timeout: 15000 }).catch(() => {});
    got.setup = await text('#pressLine');
    out.widths[width] = got;
    await ctx.close();
  }
  console.log(JSON.stringify(out));
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
