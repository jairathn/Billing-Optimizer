// The big-change banner (remitwatch.alerts SWING, static/swing.js) in a browser, on a local site over an invented store:
// is it at the top of every page, what does it say, does anything overflow at phone width, and does acknowledging it
// take it off every page. Usage: node swing_page.js <port> <user> <password> <ack: 1 or 0>. Prints one JSON line.
const { chromium } = require('playwright');
const [port, user, pass, ack] = process.argv.slice(2);
const base = `http://127.0.0.1:${port}`;
const TABS = ['today', 'denials', 'worklist', 'faxes', 'data', 'alerts', 'more'];

async function settle(page) {
  await page.waitForFunction(() => { const v = document.querySelector('#view'); return v && v.innerText.length > 20 && !/^\s*loading/.test(v.innerText); },
    null, { timeout: 20000 }).catch(() => {});
  await page.waitForFunction(() => { const b = document.querySelector('#swingBar'); return b && !b.hidden; }, null, { timeout: 8000 }).catch(() => {});
}

(async () => {
  const browser = await chromium.launch();
  const out = { pages: {}, errors: [], failed: [] };
  for (const [name, viewport] of [['desktop', { width: 1400, height: 1000 }], ['phone', { width: 390, height: 844 }]]) {
    const ctx = await browser.newContext({ viewport, httpCredentials: { username: user, password: pass } });
    const page = await ctx.newPage();
    page.on('console', m => { if (m.type() === 'error') out.errors.push(`${name} console: ${m.text()}`); });
    page.on('pageerror', e => out.errors.push(`${name} pageerror: ${e.message}`));
    page.on('response', r => { if (r.status() >= 400) out.failed.push(`${r.status()} ${r.url()}`); });
    for (const tab of TABS) {
      await page.goto(`${base}/#${tab}`);
      await settle(page);
      out.pages[`${name} #${tab}`] = await page.evaluate(() => {
        const bar = document.querySelector('#swingBar'), view = document.querySelector('#view');
        const r = bar && !bar.hidden ? bar.getBoundingClientRect() : null;
        return { shown: !!r && r.height > 0, above: !!r && r.bottom <= view.getBoundingClientRect().top + 1,
                 text: bar ? bar.innerText : '', buttons: bar ? bar.querySelectorAll('[data-swing-ack]').length : 0,
                 overflow: document.documentElement.scrollWidth - window.innerWidth,
                 bar_over: r ? Math.max(r.right - document.documentElement.clientWidth, bar.scrollWidth - bar.clientWidth) : 0 };
      });
    }
    for (const path of ['/upload', '/setup']) {
      await page.goto(base + path);
      await page.waitForFunction(() => { const b = document.querySelector('#swingBar'); return b && !b.hidden; }, null, { timeout: 10000 }).catch(() => {});
      out.pages[`${name} ${path}`] = await page.evaluate(() => {
        const bar = document.querySelector('#swingBar'), main = document.querySelector('main');
        const r = bar && !bar.hidden ? bar.getBoundingClientRect() : null;
        return { shown: !!bar && !bar.hidden, above: !!main && main.firstElementChild === bar, text: bar ? bar.innerText : '',
                 buttons: bar ? bar.querySelectorAll('[data-swing-ack]').length : 0, overflow: document.documentElement.scrollWidth - window.innerWidth,
                 bar_over: r ? Math.max(r.right - document.documentElement.clientWidth, bar.scrollWidth - bar.clientWidth) : 0 };
      });
    }
    await ctx.close();
  }
  if (ack === '1') {
    const ctx = await browser.newContext({ viewport: { width: 1400, height: 1000 }, httpCredentials: { username: user, password: pass } });
    const page = await ctx.newPage();
    const asked = [];
    page.on('dialog', d => { asked.push(d.message()); d.accept(); });
    await page.goto(`${base}/#denials`);
    await settle(page);
    await page.click('#swingBar [data-swing-ack]');
    await page.waitForFunction(() => document.querySelector('#swingBar').hidden, null, { timeout: 10000 }).catch(() => {});
    out.after_ack = { hidden: await page.evaluate(() => document.querySelector('#swingBar').hidden), asked };
    await page.goto(`${base}/#today`);
    await settle(page);
    await page.waitForTimeout(1500);
    out.after_reload = { hidden: await page.evaluate(() => document.querySelector('#swingBar').hidden) };
    await page.goto(`${base}/upload`);
    await page.waitForTimeout(1500);
    out.upload_after = { hidden: await page.evaluate(() => document.querySelector('#swingBar').hidden) };
    await ctx.close();
  }
  await browser.close();
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
