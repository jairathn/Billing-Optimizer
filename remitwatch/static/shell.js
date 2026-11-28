// RemitWatch's app shell, the same on every page (the app, Upload and Setup): the sidebar with the places; the header
// with the search, the health pill, the alerts bell and Help; the phone's tab bar and its More sheet; the theme (System,
// Light or Dark) and the sidebar's width, kept on this computer; each page's title, with the count of what closes within
// 7 days; and Search and go to (Ctrl K, or Cmd K on a Mac).
// One global name, RWShell: the pages share one scope, and Upload and Setup have their own helpers ($, api, esc).
// Browser storage holds only the theme and the sidebar's width (per-viewer conveniences), never a patient detail, and
// every read and write is wrapped so the page works without it. A page title carries counts, never patient data.
(function(){
  'use strict';
  const ICON = (n, cls) => `<svg class="i${cls ? ' ' + cls : ''}" aria-hidden="true"><use href="/static/icons.svg#i-${n}"/></svg>`;
  const escH = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
  const store = {
    get(k){ try { return localStorage.getItem(k); } catch(e){ return null; } },
    set(k, v){ try { if(v == null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch(e){} },
  };
  // The places. `tab` is the app's route (#today ...), `href` a page of its own. Work's views keep their routes; the red
  // number on a view is what closes there within 7 days (desk.js updateBadges fills [data-bdg]).
  const GROUPS = [
    {items: [
      {tab: 'today', label: 'Today', icon: 'today'},
      {tab: 'worklist', label: 'Work', icon: 'work', count: 'work', sub: [
        {tab: 'denials', label: 'Denials', bdg: true}, {tab: 'bundling', label: 'Bundling', bdg: true},
        {tab: 'downcoding', label: 'Downcoding', bdg: true}, {tab: 'contracts', label: 'Contracts', bdg: true},
        {tab: 'fees', label: 'Fees and takebacks'}]},
      {tab: 'faxes', label: 'Faxes', icon: 'fax'},
      {tab: 'results', label: 'Results', icon: 'results'}]},
    {label: 'Insights', items: [
      {tab: 'wins', label: 'Bigger wins', icon: 'wins'},
      {tab: 'progress', label: 'Progress', icon: 'health'},
      {tab: 'more', label: 'Analysis', icon: 'grid'}]},
    {label: 'Settings', items: [
      {tab: 'data', label: 'Data', icon: 'database', dot: true},
      {href: '/upload', id: 'uploadLink', label: 'Upload from EMA', icon: 'upload', hidden: true},
      {href: '/setup', id: 'setupLink', label: 'Setup', icon: 'setup'}]},
    {label: 'Owner', lock: true, items: [
      {tab: 'policy', label: 'Write-off policy', icon: 'policy'},
      {tab: 'providers', label: 'Providers', icon: 'provider'},
      {tab: 'calculator', label: 'Calculator', icon: 'calc'}]},
  ];
  // pages reached from another place: the place that holds them is the one marked
  const PARENT = {overview: 'more', alerts: 'more', explorer: 'more', payers: 'more'};
  const NAMES = {today: 'Today', worklist: 'Work', denials: 'Denials', bundling: 'Bundling and modifiers', downcoding: 'Downcoding',
    contracts: 'Contracts and underpayment', fees: 'Fees and takebacks', faxes: 'Faxes', results: 'Results', wins: 'Bigger wins',
    progress: 'Progress', more: 'Analysis', data: 'Data', policy: 'Write-off policy', providers: 'Providers', calculator: 'Calculator',
    overview: 'Overview', alerts: 'Alerts', explorer: 'Who and what', payers: 'Payers and rates', upload: 'Upload from EMA', setup: 'Setup'};
  const WORK_VIEWS = new Set(['denials', 'bundling', 'downcoding', 'contracts', 'fees']);
  const TABBAR = [{tab: 'today', label: 'Today', icon: 'today'}, {tab: 'worklist', label: 'Work', icon: 'work', count: 'work'},
    {tab: 'results', label: 'Results', icon: 'results'}, {act: 'find', label: 'Find', icon: 'search'}, {act: 'more', label: 'More', icon: 'menu'}];
  const MAC = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || '');

  let opts = {page: 'app', link: '#', live: true}, current = 'today', last = {};

  // ---------------------------------------------------------------- the sidebar, the tab bar and the More sheet
  function navItem(it, sub){
    const href = it.href || (opts.link + it.tab);
    const attrs = it.tab ? ` data-tab="${it.tab}"` : '';
    const id = it.id ? ` id="${it.id}"` : '';
    const badge = it.bdg ? `<span class="bdg" data-bdg="${it.tab}" hidden></span>` : (it.count ? `<span class="nav-count urgent" data-count="${it.count}" hidden></span>` : '');
    const dot = it.dot ? `<span class="nav-dot" data-dot="${it.tab}" hidden></span>` : '';
    return `<a class="nav-item${sub ? ' sub' : ''}" href="${href}"${id}${attrs}${it.hidden ? ' hidden' : ''} title="${escH(it.label)}">${sub ? '' : ICON(it.icon)}<span>${escH(it.label)}</span>${badge}${dot}</a>`;
  }
  function sidebarHtml(){
    const groups = GROUPS.map(g => {
      const head = g.label ? `<div class="nav-label">${g.lock ? ICON('lock') : ''}<span>${escH(g.label)}</span>${g.lock ? '<button id="lockBtn" class="ghost small" title="Forget the owner\'s password on this page: the Providers and Calculator tabs and lines billed by provider close again">Lock</button>' : ''}</div>` : '';
      return head + g.items.map(it => navItem(it) + (it.sub ? `<div class="nav-sub">${it.sub.map(s => navItem(s, true)).join('')}</div>` : '')).join('');
    }).join('');
    const theme = themeChoice();
    const seg = [['system', 'monitor', 'Follow this computer'], ['light', 'sun', 'Light'], ['dark', 'moon', 'Dark']]
      .map(([k, ic, l]) => `<button type="button" data-theme-set="${k}" aria-pressed="${theme === k}" title="${l}" aria-label="Theme: ${l}">${ICON(ic)}</button>`).join('');
    return `<a class="brand" href="${opts.link}today" data-tab="today" aria-label="RemitWatch, Today">${markSvg()}<span>RemitWatch</span></a>
      <button type="button" class="icon-btn nav-close" id="navClose" aria-label="Close the menu">${ICON('x', 'i-lg')}</button>
      ${groups}
      <div class="nav-foot">
        <div class="theme-seg" role="group" aria-label="Theme">${seg}</div>
        <button type="button" class="icon-btn" id="railBtn" title="Narrow the sidebar ([)" aria-label="Narrow or widen the sidebar">${ICON('sidebar')}</button>
      </div>`;
  }
  function tabbarHtml(){
    return TABBAR.map(t => t.tab
      ? `<a href="${opts.link}${t.tab}" data-tab="${t.tab}">${ICON(t.icon)}<span>${t.label}</span>${t.count ? `<span class="bdg" data-count="${t.count}" hidden></span>` : ''}</a>`
      : `<a href="#" data-act="${t.act}" role="button">${ICON(t.icon)}<span>${t.label}</span></a>`).join('');
  }
  // the mark: a remittance slip with a check (static/brand/icon.svg draws the same at favicon size)
  function markSvg(){
    return `<span class="brand-mark" aria-hidden="true"><svg viewBox="0 0 24 24" width="18" height="18"><path d="M6.5 3.5h11a1 1 0 0 1 1 1v15.2l-2.4-1.5-2.3 1.5-1.8-1.5-1.8 1.5-2.3-1.5-2.4 1.5V4.5a1 1 0 0 1 1-1z" fill="currentColor"/><path class="cut" d="m8.6 11.6 2.3 2.3 4.6-4.9" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg></span>`;
  }

  // ---------------------------------------------------------------- the theme: System, Light or Dark, kept on this computer
  function themeChoice(){ const t = store.get('rw:theme'); return t === 'light' || t === 'dark' ? t : 'system'; }
  function themeIsDark(){ const t = themeChoice(); return t === 'dark' || (t === 'system' && typeof matchMedia === 'function' && matchMedia('(prefers-color-scheme: dark)').matches); }
  function setTheme(k){
    if(k === 'light' || k === 'dark'){ document.documentElement.setAttribute('data-theme', k); store.set('rw:theme', k); }
    else { document.documentElement.removeAttribute('data-theme'); store.set('rw:theme', null); }
    document.querySelectorAll('[data-theme-set]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.themeSet === themeChoice())));
    themeChanged();
  }
  function themeChanged(){ if(typeof opts.onTheme === 'function') try { opts.onTheme(themeIsDark()); } catch(e){} }

  // ---------------------------------------------------------------- the sidebar's width: the person's choice, never automatic
  function setRail(on){ if(on) document.documentElement.setAttribute('data-sidebar', 'rail'); else document.documentElement.removeAttribute('data-sidebar');
    store.set('rw:sidebar', on ? 'rail' : null); }
  function toggleRail(){ setRail(document.documentElement.getAttribute('data-sidebar') !== 'rail'); }

  // ---------------------------------------------------------------- the phone's More sheet (the sidebar, over the page)
  function openSheet(on){ document.body.classList.toggle('nav-open', !!on); const n = document.getElementById('nav'); if(n) n.setAttribute('aria-modal', on ? 'true' : 'false');
    if(on){ const c = document.getElementById('navClose'); if(c) c.focus(); } }

  // ---------------------------------------------------------------- Help
  function menu(btn, box, open){ if(!btn || !box) return; const on = open == null ? box.hidden : open; box.hidden = !on; btn.setAttribute('aria-expanded', String(on));
    if(on){ const f = box.querySelector('button, a'); if(f) f.focus(); } }

  // ---------------------------------------------------------------- the health pill: what the app cannot check, in dollars
  const money = c => (c == null ? '' : (c < 0 ? '-' : '') + '$' + Math.round(Math.abs(c) / 100).toLocaleString('en-US'));
  function longDay(d){ const x = new Date(String(d || '').slice(0, 10) + 'T00:00:00Z'); return isNaN(x) ? String(d || '') : x.toLocaleDateString('en-US', {month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC'}); }
  function when(iso, tz){ const t = new Date(iso); if(!iso || isNaN(t)) return '';
    const fmt = z => t.toLocaleString('en-US', {month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZone: z});
    if(tz){ try { return fmt(tz); } catch(e){} } return fmt('UTC') + ' UTC'; }
  const FEED_SHORT = {'835': 'Remittances', ar: "EMA's A/R", production: "EMA's Production report"};
  // Everything the pill says, from the answers the page has (any may be missing): {cls, text, title, anchor}. Safe mode
  // first, then what the app cannot see in dollars, then a feed that reaches less far than the newest payment, payments
  // with no remittance, overdue EMA exports. On the Vercel site it always says when the site last analysed and that it
  // analyses only on a press (the owner's decision 1).
  function pill(x){
    x = x || {}; const meta = x.meta || {}, st = x.sync || {}, b = x.brief || {}, bl = x.blind && x.blind.blind ? x.blind : null;
    const parts = [], titles = []; let cls = 'ok', anchor = null;
    const worse = c => { const rank = {ok: 0, busy: 1, press: 1, stale: 2, safe: 3}; if(rank[c] > rank[cls]) cls = c; };
    const banner = meta.banner || null, safe = banner && (banner.safe_mode || banner.level === 'safe_mode');
    if(safe){ parts.push('Safe mode'); worse('safe'); titles.push(typeof safe === 'string' ? safe : 'Safe mode: the worklist is the one from before the last run'); }
    if(bl){
      const stale = (bl.feeds || []).filter(f => f.stale);
      const what = stale.length === 1 ? `${FEED_SHORT[stale[0].feed] || 'A feed'} ${stale[0].days != null ? stale[0].days + ' days old' : 'behind'}` : stale.length ? `${stale.length} feeds behind` : 'A feed is behind';
      parts.push(`${what} · ${money(bl.cents)} unchecked`); worse('stale');
      titles.push(...(bl.lines || []).slice(0, 2));
      anchor = stale.some(f => f.feed === '835') ? 'dtMissing' : 'dtReports';
    }
    const reach = st.feed_date && st.newest_payment_date && st.feed_date < st.newest_payment_date;
    if(!bl && (reach || (st.days_behind != null && st.days_behind > 10))){
      parts.push(reach ? `Remittances reach ${longDay(st.feed_date)} for most payers` : `No remittance since ${longDay(st.newest_payment_date)}`);
      worse('stale'); anchor = anchor || 'dtMissing';
    }
    const rm = (b.health || []).find(h => h && h.key === 'remits_missing' && h.ok === false && h.cents);
    if(rm && !bl){ parts.push(`${money(rm.cents)} paid with no remittance`); worse('stale'); titles.push(rm.title || ''); anchor = anchor || 'dtMissing'; }
    const late = ((x.exports || {}).exports || []).filter(e => e && e.overdue);
    if(late.length && cls === 'ok'){ parts.push(`${late.length} EMA ${late.length === 1 ? 'export' : 'exports'} overdue`); worse('stale'); titles.push(late.map(e => e.title).join(', ')); anchor = anchor || 'dtReports'; }
    if(st.press_only){
      // the Vercel site analyses only when someone presses Run the analysis now (Data tab): every page says when it last did
      const an = st.analysed || {}, un = st.unchecked;
      const rd = un && un.read && un.read.claims ? `${un.read.claims} remittance claim${un.read.claims === 1 ? '' : 's'} read since (${money(un.read.cents)} not paid)` : '';
      const notChecked = un && un.read_since !== false && (un.n || rd) ? [un.n ? `${un.n} open item${un.n === 1 ? '' : 's'} (${money(un.cents)})` : '', rd].filter(Boolean).join(' and ') : '';
      const press = `Analysis: ${st.running ? 'analysing now · ' : ''}${an.at ? 'last analysed ' + when(an.at, b.tz) : 'not analysed yet'} · only when someone presses Run the analysis now`
        + (st.last_failed && !st.running ? ' · the last run stopped before it finished' : '') + (notChecked ? ` · the last run could not check ${notChecked}` : '');
      parts.unshift(press); worse(st.last_failed && !st.running || notChecked ? 'stale' : 'press');
    } else if(x.freshening && cls === 'ok'){ parts.push('Looking for new payments'); cls = 'busy'; }
    if(!parts.length){ parts.push(x.metaFailed ? 'No data yet' : 'Data current'); if(x.metaFailed) cls = 'stale'; }
    if(st.newest_payment_date) titles.push(`Newest payment ${longDay(st.newest_payment_date)}${st.days_behind != null ? ` (${st.days_behind} ${st.days_behind === 1 ? 'day' : 'days'} ago)` : ''}.`);
    const run = (meta.runs || [])[0];
    if(run && run.finished_at) titles.push(`Last analysis ${when(run.finished_at, b.tz)}.`);
    return {cls, text: parts.join(' · '), title: titles.filter(Boolean).join(' '), anchor, press: !!st.press_only};
  }

  // ---------------------------------------------------------------- the page's title: the place, with what closes in 7 days
  function title(tab, x){
    const b = (x || {}).brief || {}, n = ((b.work || {}).total || {}).due7_n || 0;
    const name = NAMES[tab] || 'RemitWatch';
    const where = WORK_VIEWS.has(tab) ? `Work · ${name}` : name;
    return `${n ? `(${n}) ` : ''}${where === 'RemitWatch' ? '' : where + ' · '}RemitWatch`;
  }

  // ---------------------------------------------------------------- what the shell shows from the page's answers
  function update(x){
    last = Object.assign({}, last, x || {});
    const p = pill(last), h = document.getElementById('health'), t = document.getElementById('feedChip');
    if(h && t){ h.className = `health ${p.cls}${p.press ? ' press' : ''}`; t.textContent = p.text; h.title = p.title || '';
      h.dataset.anchor = p.anchor || ''; h.setAttribute('aria-label', `Data: ${p.text}`); }
    const alerts = last.alerts || [], hi = alerts.some(a => a && a.severity === 'high');
    const bell = document.getElementById('bellBtn');
    if(bell){ const d = bell.querySelector('.dot-count'); if(d){ d.hidden = !alerts.length; d.textContent = alerts.length > 99 ? '99+' : String(alerts.length); d.classList.toggle('quiet', !hi); }
      bell.setAttribute('aria-label', alerts.length ? `Alerts, ${alerts.length} new` : 'Alerts'); }
    const b = last.brief || {}, due7 = ((b.work || {}).total || {}).due7_n || 0;
    document.querySelectorAll('[data-count="work"]').forEach(el => { el.hidden = !due7; el.textContent = String(due7); });
    document.querySelectorAll('a[data-tab="worklist"]').forEach(a => { a.title = due7 ? `Work: ${due7} ${due7 === 1 ? 'item closes' : 'items close'} within 7 days` : 'Work'; });
    const bad = (b.health || []).filter(c => c && c.ok === false).length;
    document.querySelectorAll('[data-dot="data"]').forEach(el => { el.hidden = !bad; el.title = bad ? `${bad} data ${bad === 1 ? 'check needs' : 'checks need'} a look` : ''; });
    if(opts.page !== 'app' || current) document.title = title(opts.page === 'app' ? current : opts.page, last);
  }
  function setActive(tab){
    current = tab;
    const mark = PARENT[tab] || tab;
    document.querySelectorAll('#nav a[data-tab], #tabbar a[data-tab]').forEach(a => { const on = a.dataset.tab === mark && !a.classList.contains('brand');
      a.classList.toggle('active', on); if(on) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current'); });
    document.querySelectorAll('#nav a[data-tab="worklist"]').forEach(a => a.classList.toggle('within', WORK_VIEWS.has(mark)));
    [['upload', 'uploadLink'], ['setup', 'setupLink']].forEach(([k, id]) => { const a = document.getElementById(id); if(!a) return;
      a.classList.toggle('active', tab === k); if(tab === k){ a.setAttribute('aria-current', 'page'); a.hidden = false; } else a.removeAttribute('aria-current'); });
    const t = document.getElementById('pageTitle'); if(t) t.textContent = NAMES[tab] || '';
    document.title = title(tab, last);
    openSheet(false);
  }

  // ---------------------------------------------------------------- Search and go to (Ctrl K or Cmd K; Find on a phone)
  function palette(){
    let box = document.getElementById('palette');
    if(!box){
      box = document.createElement('div'); box.id = 'palette'; box.className = 'palette'; box.hidden = true;
      box.innerHTML = `<div class="palette-box" role="dialog" aria-modal="true" aria-label="Search and go to">
        <div class="palette-in">${ICON('search')}<input type="search" id="palQ" autocomplete="off" maxlength="200" placeholder="${opts.find ? 'Find a claim, or go to a page' : 'Go to a page'}" aria-label="Search and go to"><kbd>Esc</kbd></div>
        <div class="palette-list" id="palList" role="listbox"></div></div>`;
      document.body.appendChild(box);
      box.addEventListener('mousedown', e => { if(e.target === box) closePalette(); });
      const q = box.querySelector('#palQ');
      q.addEventListener('input', () => drawPalette(q.value));
      q.addEventListener('keydown', e => {
        const items = [...box.querySelectorAll('.palette-item')], i = items.findIndex(x => x.classList.contains('on'));
        if(e.key === 'ArrowDown' || e.key === 'ArrowUp'){ e.preventDefault(); if(!items.length) return;
          const n = e.key === 'ArrowDown' ? (i + 1) % items.length : (i - 1 + items.length) % items.length;
          items.forEach((x, k) => x.classList.toggle('on', k === n)); items[n].scrollIntoView({block: 'nearest'}); }
        else if(e.key === 'Enter'){ e.preventDefault(); const it = items[i >= 0 ? i : 0]; if(it) it.click(); }
        else if(e.key === 'Escape'){ e.preventDefault(); closePalette(); }
      });
    }
    box.hidden = false; document.body.classList.add('palette-open');
    const q = box.querySelector('#palQ'); q.value = ''; drawPalette(''); q.focus();
  }
  function closePalette(){ const box = document.getElementById('palette'); if(box) box.hidden = true; document.body.classList.remove('palette-open'); }
  function drawPalette(text){
    const box = document.getElementById('palette'); if(!box) return;
    const t = String(text || '').trim(), low = t.toLowerCase();
    const places = Object.keys(NAMES).filter(k => opts.page === 'app' || !['upload', 'setup'].includes(k))
      .map(k => ({k, name: NAMES[k]})).filter(p => !low || p.name.toLowerCase().includes(low) || p.k.includes(low));
    const rows = [];
    // a place whose name matches what was typed comes first (Enter goes there); otherwise Enter finds the claim typed
    const find = opts.find && t.length >= 3 ? `<button class="palette-item" data-find="1">${ICON('search')}<span>Find a claim: <b>${escH(t)}</b></span></button>` : '';
    if(find && !(low && places.length)) rows.push(find);
    if(places.length) rows.push(`<div class="palette-head">Go to</div>` + places.slice(0, 12).map(p => {
      const href = p.k === 'upload' ? '/upload' : p.k === 'setup' ? '/setup' : opts.link + p.k;
      return `<a class="palette-item" href="${href}" data-go="${p.k}">${ICON('arrow-r')}<span>${escH(p.name)}</span></a>`; }).join(''));
    if(find && low && places.length) rows.push(`<div class="palette-head">Claims</div>` + find);
    if(!rows.length) rows.push(`<div class="palette-empty">Nothing matches. ${opts.find ? 'A claim ID needs three characters or more.' : 'Find a claim from the app\'s search.'}</div>`);
    const list = box.querySelector('#palList'); list.innerHTML = rows.join('');
    const first = list.querySelector('.palette-item'); if(first) first.classList.add('on');
    list.querySelectorAll('.palette-item').forEach(it => it.addEventListener('click', e => {
      if(it.dataset.find){ e.preventDefault(); closePalette(); opts.find(t); return; }
      const k = it.dataset.go;
      if(opts.page === 'app' && k !== 'upload' && k !== 'setup' && typeof opts.go === 'function'){ e.preventDefault(); closePalette(); opts.go(k); }
      else closePalette();
    }));
  }

  // ---------------------------------------------------------------- mount: draw the shell and bind its controls
  function mount(o){
    opts = Object.assign({page: 'app', link: '#', live: true}, o || {});
    const nav = document.getElementById('nav'); if(!nav) return;
    nav.className = 'sidebar'; nav.setAttribute('aria-label', 'Places');
    nav.innerHTML = sidebarHtml();
    const lb = document.getElementById('lockBtn'); if(lb) lb.style.display = 'none';      // shown while the owner's password is held (providers.js)
    const tb = document.getElementById('tabbar'); if(tb) tb.innerHTML = tabbarHtml();
    nav.querySelectorAll('[data-theme-set]').forEach(b => b.addEventListener('click', () => setTheme(b.dataset.themeSet)));
    const rb = document.getElementById('railBtn'); if(rb) rb.addEventListener('click', toggleRail);
    const nc = document.getElementById('navClose'); if(nc) nc.addEventListener('click', () => openSheet(false));
    if(tb) tb.querySelectorAll('[data-act]').forEach(a => a.addEventListener('click', e => { e.preventDefault();
      if(a.dataset.act === 'more') openSheet(!document.body.classList.contains('nav-open')); else palette(); }));
    const hb = document.getElementById('helpBtn'), hm = document.getElementById('helpMenu');
    if(hb && hm){
      hb.addEventListener('click', e => { e.stopPropagation(); menu(hb, hm); });
      hm.addEventListener('click', () => menu(hb, hm, false));
      document.addEventListener('click', e => { if(!hm.hidden && !hm.contains(e.target) && e.target !== hb) menu(hb, hm, false); });
    }
    const fb = document.getElementById('findBtn'); if(fb) fb.addEventListener('click', () => palette());
    const sb = document.getElementById('searchBtn'); if(sb) sb.addEventListener('click', () => palette());      // Upload and Setup: no claim search there
    const fk = document.getElementById('findKey'); if(fk) fk.textContent = MAC ? '⌘ K' : 'Ctrl K';
    if(typeof matchMedia === 'function'){ const mq = matchMedia('(prefers-color-scheme: dark)');
      const on = () => { if(themeChoice() === 'system') themeChanged(); };
      if(mq.addEventListener) mq.addEventListener('change', on); else if(mq.addListener) mq.addListener(on); }
    document.addEventListener('keydown', e => {
      if((e.ctrlKey || e.metaKey) && !e.altKey && (e.key === 'k' || e.key === 'K')){ e.preventDefault(); palette(); return; }
      if(e.key === 'Escape'){ if(hm && !hm.hidden){ menu(hb, hm, false); hb.focus(); return; }
        const p = document.getElementById('palette'); if(p && !p.hidden){ closePalette(); return; }
        if(document.body.classList.contains('nav-open')){ openSheet(false); return; } }
      const t = e.target, typing = t && (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable);
      if(!typing && !e.ctrlKey && !e.metaKey && !e.altKey && e.key === '['){ e.preventDefault(); toggleRail(); }
      if(!typing && !e.ctrlKey && !e.metaKey && !e.altKey && e.key === '/' && sb){ e.preventDefault(); palette(); }
    });
    if(opts.page !== 'app'){ setActive(opts.page); current = opts.page; }
  }
  // Upload and Setup: the pill and the bell from the site's own answers, once the page knows the person is signed in
  async function live(){
    const get = p => fetch(p, {credentials: 'same-origin', cache: 'no-store'}).then(r => r.ok ? r.json() : null).catch(() => null);
    const [meta, sync, blind, alerts] = await Promise.all([get('/api/meta'), get('/api/sync/status'), get('/api/ema/blind'), get('/api/alerts?status=new')]);
    update({meta, sync, blind, alerts: alerts && alerts.items, metaFailed: !meta});
    const ups = document.getElementById('uploadLink'); if(ups && meta) ups.hidden = !(meta.uploads || meta.report_uploads) && opts.page !== 'upload';
    const [brief, exports] = await Promise.all([get('/api/brief'), get('/api/ema/exports')]);
    update({brief, exports});
  }

  window.RWShell = {mount, setActive, update, pill, title, palette, closePalette, setTheme, themeIsDark, live, names: NAMES, groups: GROUPS};
})();
