// The owner's "Accept what run ... found" on the Data tab (static/ops.js) on a site with an owner's password (the hook unit's
// fix round, review hook-r2-1 and hook-r2-5). Modes: 'unlocked' (the owner typed the owner's password into the Providers
// tab: state.ownerKey is set, as providers.js sets it), 'locked' (nothing typed yet: the panel must ask for it), 'look'
// (only whether the panel offers Accept at all). The confirm is answered Yes. Prints one JSON line: the accept requests
// (status, body, whether the owner's password went with it), the notice, whether the panel asks for the password, and what
// the panel offers after.
// Usage: node hook_accept_page.js <port> <user> <password> <owner password> [unlocked|locked|look]
const { chromium } = require('playwright');
const [port, user, pass, ownerPw, mode] = process.argv.slice(2);
const base = `http://127.0.0.1:${port}`;

(async () => {
  const browser = await chromium.launch();
  const out = { errors: [], accept: [] };
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 1000 }, httpCredentials: { username: user, password: pass } });
  const page = await ctx.newPage();
  page.on('pageerror', e => out.errors.push(`pageerror: ${e.message}`));
  page.on('dialog', d => d.accept());
  page.on('response', async r => { if (r.url().includes('/api/ops/selfcheck/accept')) {
    let body = null; try { body = await r.json(); } catch (e) {}
    out.accept.push({ status: r.status(), body, sent_owner: !!r.request().headers()['x-owner-password'] }); } });
  await page.goto(`${base}/#data`);
  await page.waitForSelector('#opRunning', { timeout: 30000 });
  out.offered = await page.evaluate(() => !!document.querySelector('[data-op-accept]'));
  out.running = await page.evaluate(() => ((document.getElementById('opRunning') || {}).innerText || '').replace(/\s+/g, ' ').trim().slice(0, 1500));
  if ((mode || 'unlocked') === 'look' || !out.offered) { console.log(JSON.stringify(out)); await browser.close(); return; }
  if ((mode || 'unlocked') === 'unlocked') await page.evaluate(k => { state.ownerKey = k; }, ownerPw);
  out.button = await page.evaluate(() => document.querySelector('[data-op-accept]').innerText);
  await page.click('[data-op-accept]');
  await page.waitForFunction(() => { const n = document.getElementById('rwNotice'); return (n && n.innerText.trim()) || document.querySelector('#ops-panels input[type=password]'); },
    null, { timeout: 20000 }).catch(() => {});
  out.notice = await page.evaluate(() => { const n = document.getElementById('rwNotice'); return n ? n.innerText.trim() : ''; });
  out.asks_owner_password = await page.evaluate(() => !!document.querySelector('#ops-panels input[type=password]'));
  out.ask_text = await page.evaluate(() => { const i = document.querySelector('#ops-panels input[type=password]'); return i ? i.parentElement.innerText.replace(/\s+/g, ' ').trim() : ''; });
  await page.waitForTimeout(2000);
  out.after = await page.evaluate(() => ({
    accept_again: !!document.querySelector('[data-op-accept]'),
    running: ((document.getElementById('opRunning') || {}).innerText || '').replace(/\s+/g, ' ').trim().slice(0, 1500) }));
  console.log(JSON.stringify(out));
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
