// The route card (remitwatch.routes, static/features.js xpRouteHtml) in a browser, on a local site over an invented store:
// at 1400 and 390 px, is the card in the next step above its buttons, does every filled field have its copy button, do
// Copy all and the c key copy the clean block, does anything scroll sideways, and does "I sent it" post the existing Sent
// with the channel and the reference. Usage: node routes_card_page.js <port> <user> <password> <finding id>.
// Prints one JSON line.
const { chromium } = require('playwright');
const [port, user, pass, fid] = process.argv.slice(2);
const base = `http://127.0.0.1:${port}`;

async function clip(page) {
  return page.evaluate(() => navigator.clipboard.readText());
}

(async () => {
  const browser = await chromium.launch();
  const out = { views: {}, errors: [], failed: [] };
  for (const [name, viewport] of [['desktop', { width: 1400, height: 1000 }], ['phone', { width: 390, height: 844 }]]) {
    const ctx = await browser.newContext({ viewport, httpCredentials: { username: user, password: pass },
      permissions: ['clipboard-read', 'clipboard-write'] });
    const page = await ctx.newPage();
    page.on('console', m => { if (m.type() === 'error') out.errors.push(`${name} console: ${m.text()}`); });
    page.on('pageerror', e => out.errors.push(`${name} pageerror: ${e.message}`));
    page.on('response', r => { if (r.status() >= 400) out.failed.push(`${name} ${r.status()} ${new URL(r.url()).pathname}`); });
    await page.goto(`${base}/#item=${encodeURIComponent(fid)}`);
    await page.waitForSelector('#rcCard', { timeout: 30000 });
    await page.waitForTimeout(300);
    const v = await page.evaluate(() => {
      const card = document.querySelector('#rcCard'), box = document.querySelector('#xpBox');
      const before = (a, b) => !!(a && b && (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING));
      const table = card.querySelector(':scope > table.rc-fields');
      const rows = table ? [...table.querySelectorAll('tr')] : [];
      const drawer = document.querySelector('#drawer');
      return {
        in_next_step: !!box && box.contains(card),
        above_buttons: before(card, box && box.querySelector(':scope > .todo-actions')),
        below_buttons: before(box && box.querySelector(':scope > .todo-actions'), card),
        leads: !!card.querySelector('.rc-actions button.primary'),
        below_next_line: before(box && box.querySelector(':scope > .todo-next'), card),
        head: (card.querySelector('.rc-head') || {}).innerText || '',
        main_button: ((card.querySelector('.rc-actions button') || {}).innerText || '').trim(),
        filled_rows: rows.filter(r => r.querySelector('b')).length,
        copy_buttons: table ? table.querySelectorAll('[data-rc-copy]').length : 0,
        empty_rows: rows.filter(r => !r.querySelector('b')).map(r => r.innerText.replace(/\s+/g, ' ').trim()),
        text: card.innerText,
        page_overflow: document.documentElement.scrollWidth - window.innerWidth,
        drawer_overflow: drawer ? drawer.scrollWidth - drawer.clientWidth : null,
        card_overflow: card.scrollWidth - card.clientWidth,
        sent_label: ((card.querySelector('#rcSent') || {}).innerText || '').trim(),
      };
    });
    await page.evaluate(() => navigator.clipboard.writeText(''));
    await page.click('#rcCopyAll');
    await page.waitForTimeout(200);
    v.copied = await clip(page);
    // the c key copies the same block, the panel open and nothing typed
    await page.evaluate(() => navigator.clipboard.writeText(''));
    await page.locator('#drawer h2').first().click();
    await page.keyboard.press('c');
    await page.waitForTimeout(300);
    v.keyed = await clip(page);
    // one field's own copy button copies its value only
    await page.evaluate(() => navigator.clipboard.writeText(''));
    const first = page.locator('#rcCard > table.rc-fields [data-rc-copy]').first();
    v.first_label = await first.getAttribute('aria-label');
    await first.click();
    await page.waitForTimeout(200);
    v.one = await clip(page);
    if (name === 'phone') {
      // I sent it, with the portal's submission number: the existing Sent, its note naming the channel and the number
      await page.fill('#rcRef', 'REF-TEST-1');
      const [req] = await Promise.all([
        page.waitForRequest(r => r.method() === 'POST' && /\/api\/findings\/[^/]+\/status$/.test(new URL(r.url()).pathname), { timeout: 15000 }),
        page.click('#rcSent'),
      ]);
      const res = await req.response();
      v.sent = { status: res ? res.status() : null, body: JSON.parse(req.postData() || '{}') };
      await page.waitForTimeout(500);
    }
    out.views[name] = v;
    await ctx.close();
  }
  await browser.close();
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e && e.stack || String(e)); process.exit(1); });
