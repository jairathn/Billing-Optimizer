// The route card's buttons pressed on a served site (invented store), for tests/test_routes_review.py: the c key, the
// card's main button, and (with `sent`) I sent it / I asked the payer with a reference. Usage:
//   node routes_card_press.js <port> <user> <password> <finding id> [main|sent]
// Prints one JSON line: whether the card is there and its text, what the c key copied, the main button's words, the pages
// it opened, the requests the page made to the item's API, any error the panel shows, and the request I sent it posted.
const { chromium } = require('playwright');
const [port, user, pass, fid, mode] = process.argv.slice(2);
(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 }, httpCredentials: { username: user, password: pass },
    permissions: ['clipboard-read', 'clipboard-write'] });
  const page = await ctx.newPage();
  const out = { errors: [], popups: [], requests: [], posts: [] };
  page.on('pageerror', e => out.errors.push(e.message));
  ctx.on('page', p => out.popups.push(p.url()));
  page.on('request', r => {
    const path = new URL(r.url()).pathname;
    if (/\/api\/(appeal|packets|letter)/.test(path)) out.requests.push(path);
    if (r.method() === 'POST' && /\/api\/findings\//.test(path)) out.posts.push({ path, body: JSON.parse(r.postData() || '{}') });
  });
  await page.goto(`http://127.0.0.1:${port}/#item=${encodeURIComponent(fid)}`);
  await page.waitForSelector('#xpBox', { timeout: 30000 });
  await page.waitForTimeout(400);
  out.card = await page.evaluate(() => !!document.querySelector('#rcCard'));
  out.card_text = await page.evaluate(() => (document.querySelector('#rcCard') || {}).innerText || '');
  await page.evaluate(() => navigator.clipboard.writeText(''));
  await page.locator('#drawer h2').first().click().catch(() => {});
  await page.keyboard.press('c');
  await page.waitForTimeout(300);
  out.keyed = await page.evaluate(() => navigator.clipboard.readText());
  const btn = page.locator('#rcCard .rc-actions button').first();
  out.main = out.card ? ((await btn.innerText().catch(() => '')) || '').trim() : '';
  if ((mode || 'main') === 'main' && out.main && out.main !== 'Copy all') {
    page.once('dialog', d => { out.dialog = d.message(); d.dismiss(); });
    await btn.click();
    await page.waitForTimeout(1500);
  }
  if (mode === 'sent') {
    if (await page.locator('#rcHow').count()) await page.fill('#rcHow', 'Invented payer portal');
    await page.fill('#rcRef', 'REF-TEST-2');
    await page.click('#rcSent');
    await page.waitForTimeout(1200);
    // the panel's own line, and its toast (review d-13: the panel moves on after "I asked the payer"; the toast says it saved)
    out.msg = await page.evaluate(() => ((document.querySelector('#xpMsg') || {}).textContent || '') + ' '
      + [...document.querySelectorAll('#rwToasts .t-text')].map(x => x.textContent).join(' '));
  }
  out.err = await page.evaluate(() => (document.querySelector('#xpErr') || {}).textContent || '');
  out.modal = await page.evaluate(() => { const m = document.querySelector('#modal'); return m && m.classList.contains('open') ? (document.querySelector('#modalBox') || {}).innerText.slice(0, 160) : ''; });
  console.log(JSON.stringify(out));
  await browser.close();
})().catch(e => { console.error(e && e.stack || String(e)); process.exit(1); });
