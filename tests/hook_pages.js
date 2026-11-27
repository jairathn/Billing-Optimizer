// Visit tabs of a site and record what each says above everything (the safe-mode bar, the blind-spot bar), in its view,
// and which buttons it offers (the hook unit's fix round: reviews hook-r2-2 and hook-r2-9).
// Usage: node hook_pages.js <port> <user> <password> <width> <hash,hash,...> [wait selector]
// Prints one JSON line: {tabs: {hash: {bar, text, buttons, overflow}}, errors, failed}.
const { chromium } = require('playwright');
const [port, user, pass, width, hashes, waitSel] = process.argv.slice(2);
const base = `http://127.0.0.1:${port}`;

(async () => {
  const browser = await chromium.launch();
  const out = { tabs: {}, errors: [], failed: [] };
  const ctx = await browser.newContext({ viewport: { width: Number(width || 1400), height: 1000 }, httpCredentials: { username: user, password: pass } });
  const page = await ctx.newPage();
  page.on('pageerror', e => out.errors.push(`pageerror: ${e.message}`));
  page.on('response', r => { if (r.status() >= 500) out.failed.push(`${r.status()} ${r.url()}`); });
  for (const h of (hashes || 'today').split(',')) {
    await page.goto(`${base}/#${h}`);
    await page.waitForLoadState('networkidle').catch(() => {});
    if (waitSel) await page.waitForSelector(waitSel, { timeout: 20000 }).catch(() => {});
    await page.waitForTimeout(2500);
    out.tabs[h] = await page.evaluate(() => ({
      bar: ((document.querySelector('#scBar') || {}).innerText || '').replace(/\s+/g, ' ').trim(),
      text: ((document.querySelector('#view') || document.body).innerText || '').replace(/\s+/g, ' ').trim(),
      chip: ((document.querySelector('#feedChip') || {}).innerText || '').replace(/\s+/g, ' ').trim(),
      buttons: [...document.querySelectorAll('#view button, #view a.btn')].map(b => b.innerText.replace(/\s+/g, ' ').trim()).filter(Boolean),
      overflow: document.documentElement.scrollWidth - window.innerWidth,
    }));
  }
  console.log(JSON.stringify(out));
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
