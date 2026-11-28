// The item panel in a browser (Playwright, Chromium) on an invented store, for tests/test_panel.py: no browser box anywhere
// in the panel; its head (the code, the item's place, no patient with names off); the panel takes the focus; one filled
// button; the next item's answers asked for while one is read (Next asks for nothing more); the Paid form starts empty and
// Esc closes the form before the panel; Let go needs a reason (a chip gives it) and Undo puts the status back; s and Enter
// save Sent and open the next item. Usage: node panel_page.js <port> <user> <password>; VW sets the width (1400 by default,
// 390 a phone). Prints one JSON line. Invented data only.
const { chromium } = require('playwright');
const [port, user, pass] = process.argv.slice(2);
const base = `http://127.0.0.1:${port}/`;
const sleep = ms => new Promise(ok => setTimeout(ok, ms));
(async () => {
  const browser = await chromium.launch();
  const width = Number(process.env.VW || 1400), narrow = width < 500;
  const ctx = await browser.newContext({ viewport: { width, height: narrow ? 844 : 900 }, httpCredentials: { username: user, password: pass } });
  await ctx.addInitScript(() => { try { localStorage.setItem('rw:guide:v1', '1'); } catch (e) {} });
  const page = await ctx.newPage();
  const out = { errors: [], dialogs: [] };
  page.on('pageerror', e => out.errors.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error' && !/status of (4|5)\d\d|Failed to load resource/.test(m.text())) out.errors.push('console: ' + m.text()); });
  page.on('dialog', async d => { out.dialogs.push(d.type()); await d.dismiss(); });
  const posts = [], gets = [];
  page.on('request', r => { const u = r.url().replace(base, '/');
    if (r.method() === 'POST' && /\/status$/.test(u)) posts.push({ url: u, body: JSON.parse(r.postData() || '{}') });
    if (r.method() === 'GET' && /^\/api\/findings\/[^/?]+$/.test(u)) gets.push(u); });
  const status = async id => (await (await page.request.get(base + 'api/findings/' + id)).json()).finding.status;
  const shows = (id, sel) => page.waitForFunction(([i, s]) => { const d = document.querySelector('#drawer');
    return d.classList.contains('open') && d.dataset.fid === i && !!d.querySelector(s); }, [id, sel || '#xpBox'], { timeout: 20000 });
  await page.goto(base + '#worklist'); await page.waitForSelector('#view table.wl tbody tr.row'); await sleep(300);
  const ids = await page.$$eval('#view table.wl tbody tr.row', trs => trs.map(t => t.dataset.id));
  out.ids = ids.slice(0, 4);
  await page.click(`tr.row[data-id="${ids[0]}"] td.day`); await shows(ids[0]); await sleep(1500);
  out.head = await page.$eval('#drawer .dr-head', h => h.innerText);
  out.focus = await page.evaluate(() => document.activeElement && document.activeElement.id);
  out.filled = await page.evaluate(() => {
    const probe = document.createElement('i'); probe.style.background = 'var(--accent)'; document.body.appendChild(probe);
    const accent = getComputedStyle(probe).backgroundColor; probe.remove();
    const seen = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && r.top < innerHeight && r.bottom > 0 && getComputedStyle(e).visibility !== 'hidden'; };
    const all = [...document.querySelectorAll('button, a')].filter(e => seen(e) && getComputedStyle(e).backgroundColor === accent);
    return { panel: all.filter(e => e.closest('#drawer')).length, page: all.length };
  });
  out.overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  // Next: its answers came while this item was read
  out.prefetched = gets.includes('/api/findings/' + ids[1]);
  const g0 = gets.length;
  await page.keyboard.press('j'); await shows(ids[1]);
  out.next_gets = gets.slice(g0).filter(u => u === '/api/findings/' + ids[1]).length;
  await sleep(600);
  // Paid: the form in the panel, its amount empty and focused; Esc closes the form, then the panel stays
  await page.keyboard.press('p'); await sleep(300);
  out.paid = await page.evaluate(() => ({ open: !!document.querySelector('#drawer #stForm:not([hidden]) #stAmt'), value: (document.querySelector('#drawer #stAmt') || {}).value,
    focus: document.activeElement && document.activeElement.id }));
  await page.keyboard.press('Escape'); await sleep(200);
  out.after_esc = await page.evaluate(() => ({ form: !!document.querySelector('#drawer #stForm:not([hidden])'), panel: document.querySelector('#drawer').classList.contains('open') }));
  // Let go: Save with no reason says why and sends nothing; a chip gives the reason; Undo puts the status back
  const before = await status(ids[1]);
  await page.keyboard.press('l'); await page.waitForSelector('#drawer #stSave');
  const n0 = posts.length;
  await page.click('#drawer #stSave'); await sleep(500);
  out.letgo_empty = { said: await page.$eval('#drawer #stFormErr', e => e.innerText), posts: posts.length - n0 };
  out.letgo_chip = await page.$eval('#drawer [data-chip="0"]', b => b.innerText);
  await page.click('#drawer [data-chip="0"]'); await page.click('#drawer #stSave');
  await page.waitForSelector('.toast .t-undo', { timeout: 10000 }); await sleep(400);
  out.letgo = { sent: posts.slice(n0).map(p => p.body), status: await status(ids[1]), panel: await page.$eval('#drawer', d => d.dataset.fid) };
  const n1 = posts.length;
  await page.click('.toast .t-undo'); await sleep(1200);
  out.undo = { sent: posts.slice(n1).map(p => p.body), status: await status(ids[1]), before };
  // the keyboard path: s opens Sent in the panel, Enter in its field saves, and the next item opens
  const cur = await page.$eval('#drawer', d => d.dataset.fid); await shows(cur); await sleep(500);
  await page.focus('#drawer');
  const n2 = posts.length;
  await page.keyboard.press('s'); await sleep(300);
  out.sent_form = await page.evaluate(() => document.activeElement && document.activeElement.tagName);
  await page.keyboard.type('Portal (invented)'); await page.keyboard.press('Enter'); await sleep(1500);
  out.sent = { from: cur, sent: posts.slice(n2).map(p => ({ url: p.url, status: p.body.status, seen: p.body.seen_status })), panel: await page.$eval('#drawer', d => d.dataset.fid),
    walked: (await page.$eval('#drawer', d => d.dataset.fid)) !== cur };
  // Enter on the panel presses its one filled button
  await shows(out.sent.panel); await sleep(800); await page.focus('#drawer');
  await page.evaluate(() => { window.__pressed = null; const b = document.querySelector('#drawer #xpBox .primary') || document.querySelector('#drawer #xpMain');
    if (b) b.addEventListener('click', e => { window.__pressed = b.id || b.textContent.trim(); e.stopImmediatePropagation(); e.preventDefault(); }, { capture: true, once: true }); });
  await page.keyboard.press('Enter'); await sleep(200);
  out.enter = await page.evaluate(() => window.__pressed);
  out.overflow_end = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  await browser.close();
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e && e.stack || String(e)); process.exit(1); });
