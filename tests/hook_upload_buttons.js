// On a site, whether the Feed panel (Data tab) has a press button of its own and whether the Upload page shows "Read them
// now" (review hook-r2-10: docs/VERCEL_SETUP.md names only the presses the Vercel site has).
// Usage: node hook_upload_buttons.js <port> <user> <password>. Prints one JSON line {seen: {...}}.
const { chromium } = require('playwright');
const [port, user, pass] = process.argv.slice(2);
const base = `http://127.0.0.1:${port}`;

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 1000 }, httpCredentials: { username: user, password: pass } });
  const page = await ctx.newPage();
  const seen = {};
  await page.goto(`${base}/#data`);
  await page.waitForSelector('#opAnalysis', { timeout: 30000 }).catch(() => {});
  await page.waitForTimeout(2000);
  Object.assign(seen, await page.evaluate(() => {
    const p = [...document.querySelectorAll('.panel')].find(x => /^Feed/.test((x.querySelector('h2') || {}).innerText || ''));
    return { feed_text: p ? p.innerText.replace(/\s+/g, ' ').slice(0, 300) : null, feed_button: !!(p && p.querySelector('button')) };
  }));
  seen.press_button = await page.evaluate(() => !!document.querySelector('#opAnalysis [data-op-press]'));
  await page.goto(`${base}/upload`);
  await page.waitForTimeout(3000);
  seen.upload_read_now_visible = await page.evaluate(() => { const b = document.getElementById('runNow'); return !!(b && b.offsetParent !== null); });
  console.log(JSON.stringify({ seen }));
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
