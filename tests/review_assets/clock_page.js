// Today, an item's box and the Faxes page on a server whose clock stands at 7:15 PM Eastern on an item's last day, in a
// browser whose own day (UTC) is already the next one. Usage: node clock_page.js <port> <user> <password> <due today> <faxed>
const { chromium } = require('playwright');
const [port, user, pass, dueToday, faxed] = process.argv.slice(2);
(async () => {
  const browser = await chromium.launch();
  const out = {};
  for (const [name, viewport] of [['desktop', { width: 1400, height: 1000 }], ['phone', { width: 390, height: 844 }]]) {
    const ctx = await browser.newContext({ viewport, timezoneId: 'UTC', httpCredentials: { username: user, password: pass } });
    const page = await ctx.newPage();
    await page.clock.setFixedTime(new Date('2026-11-25T00:15:00Z'));
    const errors = [], failed = [], dialogs = [];
    page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
    page.on('pageerror', e => errors.push('pageerror: ' + e.message));
    page.on('response', r => { if (r.status() >= 400) failed.push(r.status() + ' ' + r.url()); });
    page.on('dialog', async d => { dialogs.push(d.message()); await d.accept(); });
    const base = `http://127.0.0.1:${port}/`;
    await page.goto(base + '#today');
    await page.waitForFunction(() => /Today's plan/.test((document.querySelector('#view') || {}).innerText || ''), null, { timeout: 30000 });
    const r = await page.evaluate(() => ({
      header: (document.querySelector('.today-head .muted') || {}).innerText || '',
      site: siteDay(), today: closesText('2026-11-24').text, tomorrow: closesText('2026-11-25').text,
      followup: [...document.querySelectorAll('.plan .step')].map(s => s.innerText.replace(/\s+/g, ' ')).find(t => /Follow up on what you sent/.test(t)) || '',
      overflow: document.documentElement.scrollWidth - window.innerWidth,
    }));
    await page.evaluate(i => openDetail(i), dueToday);
    await page.waitForSelector('#drawer.open .todo-box', { timeout: 30000 });
    await page.waitForTimeout(400);
    r.drawer = await page.evaluate(() => ({
      head: (document.querySelector('#drawer .todo-top') || {}).innerText || '',
      next: (document.querySelector('#drawer .todo-next') || {}).innerText || '',
    }));
    await page.evaluate(() => closeDetail());
    if (name === 'desktop') {
      await page.goto(base + '#faxes');
      await page.waitForFunction(() => /Follow-ups due/.test((document.querySelector('#view') || {}).innerText || ''), null, { timeout: 30000 });
      const bt = page.locator(`button[data-send="${faxed}"]`);
      r.faxes = { button: (await bt.count()) ? await bt.innerText() : '', stale: (await bt.count()) ? await bt.getAttribute('data-stale') : null };
      if (await bt.count()) {
        await bt.click();
        await page.waitForFunction(() => ((document.querySelector('#fxPersonMsg') || {}).innerText || '').length > 0, null, { timeout: 30000 });
        r.faxes.message = await page.evaluate(() => document.querySelector('#fxPersonMsg').innerText);
      }
    }
    r.dialogs = dialogs;
    r.errors = errors;
    r.failed = failed;
    out[name] = r;
    await ctx.close();
  }
  await browser.close();
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
