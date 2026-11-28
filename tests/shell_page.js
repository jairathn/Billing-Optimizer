// The app shell in a browser (static/shell.js): Search and go to on Ctrl K, the theme kept per computer and set before
// the first paint, each page's title, the brand's icons, Upload in the shell, and the phone's tab bar and More sheet.
// Usage: node shell_page.js <port> <user> <password>. Prints one JSON line.
const { chromium } = require('playwright');
const [port, user, pass] = process.argv.slice(2);
const base = `http://127.0.0.1:${port}`;

(async () => {
  const browser = await chromium.launch();
  const out = { errors: [], failed: [] };
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 }, httpCredentials: { username: user, password: pass } });
  const page = await ctx.newPage();
  page.on('pageerror', e => out.errors.push(`pageerror: ${e.message}`));
  page.on('response', r => { if (r.status() >= 400) out.failed.push(`${r.status()} ${new URL(r.url()).pathname}`); });
  await page.goto(`${base}/#today`);
  await page.waitForSelector('#nav .nav-item', { timeout: 20000 });
  await page.waitForTimeout(800);
  // the bell (CRITIQUE_premium change 17): seven invented alerts, the five newest listed, See all, Escape closes it
  await page.evaluate(() => RWShell.update({ alerts: [1, 2, 3, 4, 5, 6, 7].map(i => ({ alert_id: 'a' + i, kind: 'RISING', severity: i === 7 ? 'high' : 'medium',
    title: 'Invented alert ' + i, payer_group: 'Plan A', last_seen: `2026-11-0${i}T12:00:00+00:00` })) }));
  await page.click('#bellBtn'); await page.waitForTimeout(250);
  out.bell = await page.evaluate(() => { const m = document.querySelector('#bellMenu'), r = m.getBoundingClientRect();
    return { shown: !m.hidden, items: [...m.querySelectorAll('.bell-item .bell-t > span:first-child')].map(s => s.textContent),
      all: m.querySelector('.bell-all').getAttribute('href'), inside: r.left >= 0 && r.right <= innerWidth && r.top >= 0,
      focus: document.activeElement && document.activeElement.classList.contains('bell-item'), hash: location.hash }; });
  await page.keyboard.press('Escape'); await page.waitForTimeout(150);
  out.bell.closes = await page.evaluate(() => document.querySelector('#bellMenu').hidden && document.activeElement && document.activeElement.id === 'bellBtn');
  out.places = await page.$$eval('#nav a.nav-item', as => as.map(a => a.dataset.tab || a.getAttribute('href')));
  out.title_today = await page.title();
  out.icons = await page.evaluate(async () => {
    const hrefs = [...document.querySelectorAll('link[rel~="icon"], link[rel="apple-touch-icon"]')].map(l => l.getAttribute('href'));
    return Promise.all(hrefs.map(async h => [h, (await fetch(h)).status]));
  });
  out.theme_colors = await page.$$eval('meta[name="theme-color"]', ms => ms.map(m => [m.getAttribute('media'), m.getAttribute('content')]));
  // Search and go to: Ctrl K, type a place, Enter
  await page.keyboard.press('Control+k');
  await page.waitForSelector('#palette:not([hidden])', { timeout: 5000 });
  out.palette_focus = await page.evaluate(() => document.activeElement && document.activeElement.id);
  await page.keyboard.type('denials');
  await page.keyboard.press('Enter');
  await page.waitForTimeout(800);
  out.after_go = { hash: await page.evaluate(() => location.hash), title: await page.title(),
    palette_hidden: await page.evaluate(() => { const p = document.querySelector('#palette'); return !p || p.hidden; }),
    active: await page.$$eval('#nav a.active', as => as.map(a => a.dataset.tab)) };
  await page.keyboard.press('Control+k'); await page.waitForTimeout(200); await page.keyboard.press('Escape'); await page.waitForTimeout(200);
  out.escape_closes = await page.evaluate(() => document.querySelector('#palette').hidden);
  // the theme: chosen once, kept on this computer, set before the first paint
  await page.click('[data-theme-set="dark"]');
  out.dark = await page.evaluate(() => [document.documentElement.getAttribute('data-theme'), localStorage.getItem('rw:theme')]);
  await page.addInitScript(() => { document.addEventListener('DOMContentLoaded', () => { window.__themeAtLoad = document.documentElement.getAttribute('data-theme'); }); });
  await page.reload(); await page.waitForSelector('#nav .nav-item');
  out.dark_at_load = await page.evaluate(() => window.__themeAtLoad);
  await page.click('[data-theme-set="system"]');
  out.system = await page.evaluate(() => [document.documentElement.getAttribute('data-theme'), localStorage.getItem('rw:theme')]);
  // Upload in the shell
  await page.goto(`${base}/upload`);
  await page.waitForSelector('#nav .nav-item', { timeout: 20000 }); await page.waitForTimeout(1200);
  out.upload = { title: await page.title(), places: await page.$$eval('#nav a.nav-item', as => as.length),
    active: await page.$$eval('#nav a.active', as => as.map(a => a.id)), pill: await page.$eval('#feedChip', e => e.innerText.trim()) };
  await page.keyboard.press('Control+k'); await page.waitForTimeout(300);
  out.upload_goto = await page.$$eval('#palette a.palette-item', as => as.map(a => a.getAttribute('href')).slice(0, 3));
  await ctx.close();
  // a phone: the tab bar, Find and More
  const pctx = await browser.newContext({ viewport: { width: 390, height: 844 }, httpCredentials: { username: user, password: pass } });
  const ph = await pctx.newPage();
  ph.on('pageerror', e => out.errors.push(`phone pageerror: ${e.message}`));
  await ph.goto(`${base}/#today`); await ph.waitForSelector('#tabbar a', { timeout: 20000 }); await ph.waitForTimeout(800);
  out.phone = { tabs: await ph.$$eval('#tabbar a', as => as.map(a => a.innerText.trim())), sidebar_shown: await ph.$eval('#nav', n => n.getBoundingClientRect().width > 0),
    overflow: await ph.evaluate(() => document.documentElement.scrollWidth - window.innerWidth) };
  await ph.click('#tabbar [data-act="find"]'); await ph.waitForTimeout(300);
  out.phone.find_opens = await ph.evaluate(() => !document.querySelector('#palette').hidden);
  await ph.keyboard.press('Escape'); await ph.waitForTimeout(200);
  await ph.click('#tabbar [data-act="more"]'); await ph.waitForTimeout(300);
  out.phone.more_opens = await ph.evaluate(() => document.body.classList.contains('nav-open') && document.querySelector('#nav').getBoundingClientRect().width > 300);
  await ph.keyboard.press('Escape'); await ph.waitForTimeout(200);
  out.phone.more_closes = await ph.evaluate(() => !document.body.classList.contains('nav-open'));
  await pctx.close();
  console.log(JSON.stringify(out));
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
