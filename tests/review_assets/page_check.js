// Drive the dashboard on a local server over the invented store built by page_store.py. Reports injected elements,
// what the new parts show, and every console error. Usage: node page_check.js <port> <outdir>
const { chromium } = require('playwright');
const port = process.argv[2], OUT = process.argv[3] + '/';
(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 1000 } });
  const page = await ctx.newPage();
  const errors = [], failed = [], dialogs = [];
  page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('response', r => { if (r.status() >= 400) failed.push(r.status() + ' ' + r.url()); });
  page.on('dialog', async d => { dialogs.push(d.type() + ': ' + d.message()); await d.dismiss(); });
  const base = `http://127.0.0.1:${port}/`;
  const settle = async () => { await page.waitForTimeout(600); await page.waitForFunction(() => !/loading/.test((document.querySelector('#view') || {}).innerText || '')); };
  const injected = () => page.evaluate(() => ({
    ids: ['payerx', 'planx', 'pcnx', 'notex', 'actorx'].filter(id => document.getElementById(id)),
    imgs: document.querySelectorAll('#view img, #drawer img, #modalBox img').length,
  }));
  const out = { injected: {} };

  await page.goto(base + '#overview'); await settle();          // the classic overview (the page opens on Today)
  await page.selectOption('#statusSel', 'all'); await settle();
  out.injected.overview = await injected();

  await page.goto(base + '#contracts'); await settle();
  out.injected.contracts = await injected();
  out.contracts_warnboxes = await page.evaluate(() => document.querySelectorAll('#view .warnbox').length);
  out.underpaid_rows = await page.evaluate(() => [...document.querySelectorAll('#view table.wl tbody tr')].map(t => t.innerText.replace(/\s+/g, ' ').slice(0, 220)));
  out.hint_chips = await page.evaluate(() => [...document.querySelectorAll('#view .chip.hint')].map(c => c.innerText));

  // the category page lists short labels, so the item is found by its title through the list's own call and opened as a row opens it
  const drawerFor = async (text, category = 'underpayment') => {
    const id = await page.evaluate(async ([t, cat]) => { const r = await (await fetch('/api/findings?status=all&limit=2000&category=' + cat)).json();
      const hit = r.items.find(x => (x.title || '').includes(t)); return hit ? hit.finding_id : null; }, [text, category]);
    if (!id) throw new Error('no finding titled ' + text);
    await page.evaluate(i => openDetail(i), id); await page.waitForSelector('#drawer.open h2'); await page.waitForTimeout(500);
    const r = await page.evaluate(() => ({
      title: document.querySelector('#drawer h2').innerText,
      notes: [...document.querySelectorAll('#drawer .note')].map(n => n.innerText.replace(/\s+/g, ' ')),
      appeal_button: !!document.querySelector('#drawer #btnAppeal'),
      claim_rows: [...document.querySelectorAll('#drawer table.small tbody tr')].map(t => t.innerText.replace(/\s+/g, ' ')).filter(t => /^CB|^</.test(t)),
      history: [...document.querySelectorAll('#drawer h3.small')].map(h => h.innerText),
    }));
    r.injected = await injected();
    await page.click('#closeD');
    return r;
  };
  out.plan_pattern = await drawerFor('99214 at $90.00');
  out.aetna_pattern = await drawerFor('Aetna allowed 17311');
  out.legacy_pattern = await drawerFor('(earlier release)', 'contract');
  await page.screenshot({ path: OUT + 'contracts.png', fullPage: true });

  await page.goto(base + '#denials'); await settle();
  out.injected.denials = await injected();
  out.denial_hints = await page.evaluate(() => [...document.querySelectorAll('#view .chip.hint')].map(c => c.innerText));
  await page.goto(base + '#appeals'); await settle();
  out.injected.appeals = await injected();
  out.appeals_rows = await page.evaluate(() => [...document.querySelectorAll('#view table.wl tbody tr')].map(t => t.innerText.replace(/\s+/g, ' ').slice(0, 160)));
  // Fees & takebacks: the recoupment dispute and the fee opt-out letters
  await page.goto(base + '#fees'); await settle();
  out.injected.fees = await injected();
  out.fees_drawers = {};
  // a takeback opens its drawer from the list of every takeback; a payer's payment fees open that payer's request letter
  await page.locator('#view .tile[data-paid=""]').first().click(); await settle();
  out.fees_rows = await page.evaluate(() => [...document.querySelectorAll('#view table.wl tbody tr')].map(t => t.innerText.replace(/\s+/g, ' ').slice(0, 140)));
  const tbRow = page.locator('#view table.wl tbody tr.row').first();
  if (await tbRow.count()) {
    await tbRow.click(); await page.waitForSelector('#drawer.open h2'); await page.waitForTimeout(500);
    out.fees_drawers.TAKEBACK = await page.evaluate(() => ({ title: document.querySelector('#drawer h2').innerText,
      appeal_button: !!document.querySelector('#drawer #btnAppeal'), buttons: [...document.querySelectorAll('#drawer .actions button')].map(b => b.innerText) }));
    await page.click('#closeD');
  } else out.fees_drawers.TAKEBACK = 'row not found';
  await page.locator('#view .tile[data-l="fees"]').first().click(); await settle();
  const feeRow = page.locator('#view tr.fee-row').first();
  if (await feeRow.count()) {
    await feeRow.click(); await page.waitForSelector('#modal.open #letter'); await page.waitForTimeout(300);
    out.fees_drawers.FEE_SKIM = await page.evaluate(() => ({ title: document.querySelector('#modalBox h2').innerText,
      appeal_button: !!document.querySelector('#modal.open #letter'), letter: document.querySelector('#modal #letter').innerText,
      buttons: [...document.querySelectorAll('#modalBox .actions button')].map(b => b.innerText) }));
    await page.click('#closeM');
  } else out.fees_drawers.FEE_SKIM = 'row not found';
  out.fee_letters = await page.evaluate(async () => {
    const res = await (await fetch('/api/findings?category=fees&status=all')).json();
    const out = {};
    for (const it of res.items) { const p = await (await fetch('/api/appeal/' + it.finding_id)).json(); out[it.signal] = p.subject; }
    return out;
  });
  await page.goto(base + '#progress'); await settle();
  out.injected.progress = await injected();
  await page.goto(base + '#explorer'); await settle();
  out.injected.explorer = await injected();

  console.log(JSON.stringify({ out, errors, failed, dialogs }));
  await browser.close();
})().catch(e => { console.error('SCRIPT FAILED', e); process.exit(1); });
