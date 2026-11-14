// The Data tab on a local server over an invented store: is the "EMA charge exports that miss visits" panel there, what
// it lists, console errors, failed requests, and horizontal overflow at phone width. Usage: node r6cov_page.js <port> <user> <password>
const { chromium } = require('playwright');
const [port, user, pass] = process.argv.slice(2);
(async () => {
  const browser = await chromium.launch();
  const out = {};
  for (const [name, viewport] of [['desktop', { width: 1400, height: 1000 }], ['phone', { width: 390, height: 844 }]]) {
    const ctx = await browser.newContext({ viewport, httpCredentials: { username: user, password: pass } });
    const page = await ctx.newPage();
    const errors = [], failed = [];
    page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
    page.on('pageerror', e => errors.push('pageerror: ' + e.message));
    page.on('response', r => { if (r.status() >= 400) failed.push(r.status() + ' ' + r.url()); });
    await page.goto(`http://127.0.0.1:${port}/#data`);
    await page.waitForFunction(() => /What is loaded/.test((document.querySelector('#view') || {}).innerText || ''), null, { timeout: 20000 });
    await page.waitForTimeout(500);
    out[name] = await page.evaluate(() => {
      const panels = [...document.querySelectorAll('#view .panel')];
      const ema = panels.find(p => /EMA charge exports that miss visits/.test((p.querySelector('h2') || {}).innerText || ''));
      return {
        panel: !!ema,
        rows: ema ? [...ema.querySelectorAll('tbody tr')].map(t => [...t.querySelectorAll('td')].map(td => td.innerText.trim())) : [],
        order: panels.map(p => (p.querySelector('h2') || {}).innerText || '').filter(Boolean).slice(0, 4),
        overflow: document.documentElement.scrollWidth - window.innerWidth,
      };
    });
    out[name].errors = errors;
    out[name].failed = failed;
    await ctx.close();
  }
  await browser.close();
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
