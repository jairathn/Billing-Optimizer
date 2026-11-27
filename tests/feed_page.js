// How far the remittances reach, in a browser (static/today.js's banner, static/desk.js's warning line), on a local site
// over an invented store. Usage: node feed_page.js <port> <user> <password>. Prints one JSON line.
const { chromium } = require('playwright');
const [port, user, pass] = process.argv.slice(2);
const base = `http://127.0.0.1:${port}`;

(async () => {
  const browser = await chromium.launch();
  const out = { errors: [], failed: [], banner: '', line: '' };
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 1000 }, httpCredentials: { username: user, password: pass } });
  const page = await ctx.newPage();
  page.on('console', m => { if (m.type() === 'error') out.errors.push(`console: ${m.text()}`); });
  page.on('pageerror', e => out.errors.push(`pageerror: ${e.message}`));
  page.on('response', r => { if (r.status() >= 400) out.failed.push(`${r.status()} ${r.url()}`); });
  await page.goto(`${base}/#today`);
  await page.waitForFunction(() => { const v = document.querySelector('#view'); return v && /Today's plan|Nothing analysed/.test(v.innerText); },
    null, { timeout: 20000 }).catch(() => {});
  // Today draws the page, then holds the feed's check back from its health banner until the blind spot is known (the
  // counts' answer, static/today.js seen()); both banners are final from that moment. Read once the health banner says
  // where the remittances reach (or after the wait, whatever it says): reading right after the plan appeared caught the
  // page before that answer, now and then, under load
  await page.waitForFunction(() => { const h = document.querySelector('#view #tdHealth .banner'); return h && /The remittances reach/.test(h.innerText); },
    null, { timeout: 20000 }).catch(() => {});
  out.banner = await page.evaluate(() => { const b = document.querySelector('#view .banner'); return b ? b.innerText.replace(/\s+/g, ' ').trim() : ''; });
  await page.goto(`${base}/#worklist`);
  await page.waitForFunction(() => { const v = document.querySelector('#view'); return v && v.querySelector('.tiles'); }, null, { timeout: 20000 }).catch(() => {});
  // the warning line comes with the Worklist's own answer about the feed: read once it is drawn (or after the wait)
  await page.waitForFunction(() => { const l = document.querySelector('#view .oneline'); return l && /not all in yet/.test(l.innerText); },
    null, { timeout: 20000 }).catch(() => {});
  out.line = await page.evaluate(() => { const l = document.querySelector('#view .oneline'); return l ? l.innerText.replace(/\s+/g, ' ').trim() : ''; });
  await ctx.close();
  await browser.close();
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
