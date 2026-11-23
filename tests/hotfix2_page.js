// Hotfix 2, the pages in a browser (Playwright, Chromium) on a local server over an invented store: the item panel acts on
// the item it shows and once (H1), every status change carries what the page showed (H3), the Paid amount is read as
// typed (H4), the note check asks a person (H6), hiding shows its size and asks for the owner and the count (H7), the
// header says how far the remittances reach (H8), Find a claim says when its copy is partial (H10), and the address bar
// takes only RemitWatch's own names (H11). Usage: node hotfix2_page.js <port> <user> <password> <owner password>
const { chromium } = require('playwright');
const [port, user, pass, owner] = process.argv.slice(2);
const base = `http://127.0.0.1:${port}/`;
const sleep = ms => new Promise(ok => setTimeout(ok, ms));

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 1000 }, httpCredentials: { username: user, password: pass } });
  const page = await ctx.newPage();
  const out = { errors: [] };
  page.on('pageerror', e => out.errors.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error' && !/the server responded with a status of (4|5)\d\d|Failed to load resource/.test(m.text())) out.errors.push('console: ' + m.text()); });
  // every status change, appeal send and hide the page makes, with its body and headers
  const posts = [];
  page.on('request', r => { if (r.method() === 'POST' && /\/api\/(findings|appeals\/send|hide)/.test(r.url())) {
    let body = null; try { body = JSON.parse(r.postData() || 'null'); } catch (e) { body = r.postData(); }
    posts.push({ url: r.url().replace(base, '/'), body, owner: !!r.headers()['x-owner-password'] }); } });
  const answers = [], dialogs = [];          // what a person types into each prompt, in order; what each one said
  page.on('dialog', async d => { dialogs.push({ type: d.type(), message: d.message(), value: d.defaultValue() });
    const a = answers.length ? answers.shift() : null;
    if (a === null) await d.dismiss(); else await d.accept(a === true ? undefined : a); });
  const status = async id => (await (await page.request.get(base + 'api/findings/' + id)).json()).finding.status;
  const open = async () => { await page.goto(base + '?load=' + Date.now() + '#worklist'); await page.waitForSelector('#view table.wl tbody tr.row'); await sleep(300); };
  const rows = () => page.$$eval('#view table.wl tbody tr.row', trs => trs.map(t => t.dataset.id));
  const settle = async id => { await page.waitForFunction(i => { const d = document.querySelector('#drawer'); return d && d.classList.contains('open') && d.dataset.fid === i && !!d.querySelector('h2'); }, id, { timeout: 15000 }); };

  // ---------------------------------------------------------------- H11: the address bar takes only RemitWatch's names
  out.hash = {};
  for (const h of ['constructor', '__proto__', 'toString', 'valueOf', 'hasOwnProperty']) {
    await page.goto(base + '#' + h); await page.waitForFunction(() => !/loading/.test((document.querySelector('#view') || {}).innerText || '')); await sleep(200);
    out.hash[h] = await page.evaluate(() => ({ hash: location.hash, active: (document.querySelector('nav a.active') || {}).dataset ? document.querySelector('nav a.active').dataset.tab : null }));
  }

  // ---------------------------------------------------------------- H1 (0a-2): a double or triple click on "Mark sent and open the next"
  await open();
  let ids = await rows();
  out.walk = { ids: ids.slice(0, 5) };
  await page.click(`tr.row[data-id="${ids[0]}"] td.day`); await settle(ids[0]); await sleep(900);
  const sn = await page.$('#sentNext'); const box = await sn.boundingBox();
  await page.mouse.click(box.x + 5, box.y + 5); await sleep(130); await page.mouse.click(box.x + 5, box.y + 5);
  await sleep(1500);
  out.walk.double = [await status(ids[0]), await status(ids[1]), await status(ids[2])];
  out.walk.panel_after_double = await page.$eval('#drawer', d => d.dataset.fid);
  await sleep(900);
  const sn2 = await page.$('#sentNext'); const b2 = await sn2.boundingBox();
  for (let k = 0; k < 3; k++) { await page.mouse.click(b2.x + 5, b2.y + 5); await sleep(150); }
  await sleep(1500);
  out.walk.triple = [await status(ids[1]), await status(ids[2]), await status(ids[3])];
  out.walk.posts = posts.filter(p => /\/status$/.test(p.url)).map(p => ({ id: p.url.split('/')[3], status: p.body.status, seen: p.body.seen_status }));

  // ---------------------------------------------------------------- H1 (0a-7, 0d-4): the first answer is slow; the panel shows the last row clicked
  await open(); ids = await rows();
  const slow = ids[0], last = ids[3];
  await page.route('**/api/findings/' + slow, async route => { await sleep(1500); await route.continue(); });
  await page.click(`tr.row[data-id="${slow}"] td.day`); await sleep(150); await page.click(`tr.row[data-id="${last}"] td.day`);
  await sleep(2600);
  out.race = { panel: await page.$eval('#drawer', d => d.dataset.fid), last, slow };
  await page.unroute('**/api/findings/' + slow);

  // ---------------------------------------------------------------- H1 (0a-10): two clicks 20 ms apart on the status bar's Sent: one change
  const n0 = posts.length;
  const sent = await page.$('#drawer [data-st="appealed"]'); const sb = await sent.boundingBox();
  await page.mouse.click(sb.x + 5, sb.y + 5); await sleep(20); await page.mouse.click(sb.x + 5, sb.y + 5);
  await sleep(1500);
  out.double_sent = { posts: posts.slice(n0).filter(p => /\/status$/.test(p.url)).length, events: (await (await page.request.get(base + 'api/findings/' + last)).json()).events.filter(e => e.kind === 'manual').length };

  // ---------------------------------------------------------------- H1 (0d-1): a late "Appeal by fax" answer never rebinds another item's panel
  await open(); ids = await rows();
  const A = ids[0], B = ids[1];
  const sends = [];
  await page.route('**/api/appeals/send', async route => {
    const body = JSON.parse(route.request().postData()); sends.push(body);
    if (body.finding_id === A && !body.stale_ok) await sleep(1500);
    const held = { finding_id: body.finding_id, status: 'held', code: 'stale', reason: 'The newest remittance RemitWatch has is 45 days old, so this claim may have been paid since.' };
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body.stale_ok ? { finding_id: body.finding_id, status: 'sent', number: '(800) 555-0142' } : held) });
  });
  await page.click(`tr.row[data-id="${A}"] td.day`); await settle(A); await page.click('#drawer #fxOne');
  await sleep(100);
  await page.click(`tr.row[data-id="${B}"] td.day`); await settle(B); await page.click('#drawer #fxOne');
  await page.waitForSelector('#drawer #fxStale'); await sleep(2000);         // A's late answer arrives meanwhile
  out.cross = { panel: await page.$eval('#drawer', d => d.dataset.fid), A, B, stale_buttons: await page.$$eval('#drawer #fxStale', x => x.length) };
  await page.click('#drawer #fxStale'); await sleep(800);
  out.cross.sends = sends.map(s => ({ id: s.finding_id === A ? 'A' : s.finding_id === B ? 'B' : '?', stale_ok: !!s.stale_ok }));
  out.cross.notice = await page.$$eval('#rwNotice div', ds => ds.map(d => d.innerText));
  await page.unroute('**/api/appeals/send');

  // ---------------------------------------------------------------- H1 (0d-5): a fax that goes out does not pull the panel back; typing is kept
  await open(); ids = await rows();
  const A2 = ids[2], B2 = ids[3];
  await page.route('**/api/appeals/send', async route => { const body = JSON.parse(route.request().postData()); await sleep(1500);
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ finding_id: body.finding_id, status: 'sent', number: '(800) 555-0142' }) }); });
  await page.click(`tr.row[data-id="${A2}"] td.day`); await settle(A2); await page.click('#drawer #fxOne');
  await page.click(`tr.row[data-id="${B2}"] td.day`); await settle(B2);
  await page.evaluate(() => { const f = document.querySelector('#drawer details.dfold:has(#noteText)'); if (f) f.open = true; });
  await page.fill('#drawer #noteText', 'Text typed for the second item');
  await sleep(2200);
  out.yank = { panel: await page.$eval('#drawer', d => d.dataset.fid), B: B2, typed: await page.$eval('#drawer #noteText', t => t.value),
               notice: await page.$$eval('#rwNotice div', ds => ds.map(d => d.innerText)) };
  await page.unroute('**/api/appeals/send');

  // ---------------------------------------------------------------- H1: a double click on "Build the packet", then on the packet's Send: once each
  await open(); ids = await rows();
  const once = { builds: 0, emails: 0 };
  await page.route('**/api/appeals/build', async route => { once.builds++; await sleep(800);
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ packet_id: 'pkt.2', mode: 'template',
      analysis: { mode: 'template', reason: 'A letter from the template.' }, letter: 'An invented letter.' }) }); });
  await page.route('**/api/packets/pkt.2/email', async route => { once.emails++; await sleep(800);
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ sent: true, to: 'billing@example.org' }) }); });
  await page.click(`tr.row[data-id="${ids[3]}"] td.day`); await settle(ids[3]);
  await page.evaluate(() => { const f = document.querySelector('#drawer details.dfold:has(#noteText)'); if (f) f.open = true; });
  await page.fill('#drawer #noteText', 'An invented visit note.');
  await page.dblclick('#drawer #noteGo'); await page.waitForSelector('#drawer #pkSend', { timeout: 10000 });
  await page.fill('#drawer #pkTo', 'billing@example.org'); await page.dblclick('#drawer #pkSend'); await sleep(1500);
  out.once = once;
  await page.unroute('**/api/appeals/build'); await page.unroute('**/api/packets/pkt.2/email');

  // ---------------------------------------------------------------- H6: the note check asks a person, and the tick goes with the send
  await page.route('**/api/appeals/send', async route => { const body = JSON.parse(route.request().postData()); sends.push(body);
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body.note_checked === true
      ? { finding_id: body.finding_id, status: 'sent', number: '(800) 555-0142' }
      : { finding_id: body.finding_id, status: 'held', code: 'note_check', confirm: true, visit_date: '2026-11-01', packet_id: 'pkt.1',
          reason: "Check the visit note before it goes: Its text does not show this patient's name." }) }); });
  await open(); ids = await rows();
  await page.click(`tr.row[data-id="${ids[4]}"] td.day`); await settle(ids[4]);
  sends.length = 0;
  await page.click('#drawer #fxOne'); await page.waitForSelector('#drawer #fxNoteOk');
  out.note = { disabled_before: await page.$eval('#drawer #fxNoteGo', b => b.disabled), label: await page.$eval('#drawer #fxNoteOk', c => c.parentElement.innerText),
               packet_link: await page.$$eval('#drawer a[href*="/api/packets/pkt.1/pdf"]', a => a.length) };
  await page.check('#drawer #fxNoteOk'); out.note.disabled_after = await page.$eval('#drawer #fxNoteGo', b => b.disabled);
  await page.click('#drawer #fxNoteGo'); await sleep(800);
  out.note.sends = sends.map(s => ({ note_checked: s.note_checked, packet_id: s.packet_id || null }));
  await page.unroute('**/api/appeals/send');

  // ---------------------------------------------------------------- H4: the Paid amount
  await open(); ids = await rows();
  const paid = [];
  for (const [k, typed] of [[5, ['1,250.00']], [6, ['', '-50', '1e9', '12.345', 'abc', '0', '$0.00', '$1,250.00']], [7, ['1250.5']], [8, ['$95']], [9, ['99999']]]) {
    await page.click(`tr.row[data-id="${ids[k]}"] td.day`); await settle(ids[k]); await sleep(300);
    dialogs.length = 0; answers.length = 0; answers.push(...typed, null);
    const before = posts.length;
    await page.click('#drawer [data-st="resolved"]'); await sleep(900);
    const p = posts.slice(before).find(x => /\/status$/.test(x.url));
    paid.push({ typed, asked: dialogs.map(d => ({ value: d.value, message: d.message })), sent: p ? p.body : null });
    answers.length = 0;
  }
  out.paid = paid;

  // ---------------------------------------------------------------- H3: a bulk change carries what each row showed
  await open(); ids = await rows();
  answers.length = 0; answers.push(true, true);
  await page.check(`tr.row[data-id="${ids[0]}"] input.sel`); await page.check(`tr.row[data-id="${ids[1]}"] input.sel`);
  const before = posts.length;
  await page.click('#view [data-bulk="in_progress"]'); await sleep(1200);
  out.bulk = posts.slice(before).filter(x => /bulk-status$/.test(x.url)).map(x => x.body);

  // ---------------------------------------------------------------- H7: hiding shows its size first, asks for the reason, the count and the owner
  await open(); ids = await rows();
  await page.click(`tr.row[data-id="${ids[2]}"] td.day`); await settle(ids[2]);
  out.hide = {};
  if (await page.$('#drawer #hideFromItem')) {
    await page.click('#drawer #hideFromItem'); await page.waitForSelector('#modalBox #hdNote');
    out.hide.text = await page.$eval('#modalBox', b => b.innerText.replace(/\s+/g, ' ').slice(0, 600));
    out.hide.note_prefilled = await page.$eval('#modalBox #hdNote', t => t.value);
    await page.fill('#modalBox #hdNote', 'This payer does not pay these');
    await page.fill('#modalBox #hdCount', '999'); await page.click('#modalBox #hdGo'); await sleep(300);
    out.hide.wrong_count = await page.$eval('#modalBox #hdMsg', m => m.innerText);
    const want = (out.hide.text.match(/lets go (\d+) open item/) || [])[1];
    await page.fill('#modalBox #hdCount', want || ''); const hb = posts.length;
    await page.click('#modalBox #hdGo'); await page.waitForSelector('#modalBox #hdMsg input[type=password]', { timeout: 5000 }).catch(() => {});
    out.hide.asked_owner = !!(await page.$('#modalBox #hdMsg input[type=password]'));
    if (out.hide.asked_owner) { await page.fill('#modalBox #hdMsg input[type=password]', owner); answers.length = 0; answers.push(true); await page.click('#modalBox #hdMsg [data-ok]'); await sleep(1500); }
    out.hide.posts = posts.slice(hb).filter(x => /\/api\/hide$/.test(x.url)).map(x => ({ body: x.body, owner: x.owner }));
    out.hide.status_after = await status(ids[2]);
  }

  // ---------------------------------------------------------------- H8: the header says how far the remittances reach
  await page.route('**/api/sync/status', route => route.fulfill({ status: 200, contentType: 'application/json',
    body: JSON.stringify({ newest_payment_date: '2026-11-10', feed_date: '2026-09-01', days_behind: 83, mode: 'server', dropbox_configured: false }) }));
  await page.goto(base + '#today'); await sleep(1500);
  out.header = await page.$eval('#feedChip', c => c.innerText);
  await page.unroute('**/api/sync/status');

  // ---------------------------------------------------------------- H10: Find a claim on a partial copy says so
  await page.route('**/api/claims/search', route => route.fulfill({ status: 200, contentType: 'application/json',
    body: JSON.stringify({ items: [], more: false, partial: 'The copy of the data on this site holds only the claims that have items, so a paid claim with no item does not show here. Look it up in EMA.' }) }));
  await page.fill('#claimFind', 'ZZ99999999'); await page.press('#claimFind', 'Enter'); await sleep(800);
  out.find_partial = await page.$eval('#drawer', d => d.innerText);
  await page.unroute('**/api/claims/search');
  await page.fill('#claimFind', 'ZZ99999999'); await page.press('#claimFind', 'Enter'); await sleep(800);
  out.find_server = await page.$eval('#drawer', d => d.innerText);

  await browser.close();
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
