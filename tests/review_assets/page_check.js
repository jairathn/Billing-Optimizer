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

  await page.goto(base); await settle();
  await page.selectOption('#statusSel', 'all'); await settle();
  out.injected.overview = await injected();

  await page.goto(base + '#contracts'); await settle();
  out.injected.contracts = await injected();
  out.contracts_warnboxes = await page.evaluate(() => document.querySelectorAll('#view .warnbox').length);
  out.underpaid_rows = await page.evaluate(() => [...document.querySelectorAll('#tblU tbody tr')].map(t => t.innerText.replace(/\s+/g, ' ').slice(0, 220)));
  out.hint_chips = await page.evaluate(() => [...document.querySelectorAll('#view .chip.hint')].map(c => c.innerText));

  const drawerFor = async (text, tbl = '#tblU') => {
    const row = page.locator(tbl + ' tbody tr', { hasText: text }).first();
    await row.click(); await page.waitForSelector('#drawer.open h2'); await page.waitForTimeout(500);
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
  out.legacy_pattern = await drawerFor('(earlier release)', '#tblC');
  await page.screenshot({ path: OUT + 'contracts.png', fullPage: true });

  await page.goto(base + '#denials'); await settle();
  out.injected.denials = await injected();
  out.denial_hints = await page.evaluate(() => [...document.querySelectorAll('#view .chip.hint')].map(c => c.innerText));
  await page.goto(base + '#appeals'); await settle();
  out.injected.appeals = await injected();
  out.appeals_rows = await page.evaluate(() => [...document.querySelectorAll('#tbl tbody tr')].map(t => t.innerText.replace(/\s+/g, ' ').slice(0, 160)));
  // Fees & takebacks: the recoupment dispute and the fee opt-out letters
  await page.goto(base + '#fees'); await settle();
  out.injected.fees = await injected();
  out.fees_rows = await page.evaluate(() => [...document.querySelectorAll('#tbl tbody tr')].map(t => t.innerText.replace(/\s+/g, ' ').slice(0, 140)));
  out.fees_drawers = {};
  for (const [label, text] of [['TAKEBACK', 'Overpayment recovery'], ['FEE_SKIM', 'payment fees withheld']]) {
    const row = page.locator('#tbl tbody tr', { hasText: text }).first();
    if (!(await row.count())) { out.fees_drawers[label] = 'row not found'; continue; }
    await row.click(); await page.waitForSelector('#drawer.open h2'); await page.waitForTimeout(500);
    const fid = await page.evaluate(() => document.querySelector('#drawer [data-st]') && [...document.querySelectorAll('tr.row')].length);
    out.fees_drawers[label] = await page.evaluate(() => ({ title: document.querySelector('#drawer h2').innerText,
      appeal_button: !!document.querySelector('#drawer #btnAppeal'), buttons: [...document.querySelectorAll('#drawer .actions button')].map(b => b.innerText) }));
    await page.click('#closeD');
  }
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
