/* The big-change banner: one analysis run added or removed far more open items or dollars than a run normally does
   (remitwatch.alerts, SWING). It sits at the top of every page (the dashboard, the Upload page, the setup page) until a
   person acknowledges it or seven days pass, and says what moved, how many files were read and what to do. It shows only
   what the alert says: counts, dollars, times and plain words, never a file name or a patient detail.
   Standalone on purpose: each page loads it with nothing else. The dashboard calls swingShow() with the alerts its header
   already fetched; the other pages call swingLoad(). */
(function(){
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const CSS = '.swing{border:1px solid var(--line,#e3e7ec);border-left:6px solid var(--bad,#b42318);background:var(--panel,#fff);color:var(--ink,#17202a);'
    + 'border-radius:10px;padding:10px 14px;margin:12px 0;overflow-wrap:anywhere}'
    + '.swing.info{border-left-color:var(--warn,#b54708)}'
    + '.swing .sw-k{display:block;font-size:12px;font-weight:700;color:var(--bad,#b42318);margin-bottom:2px}'
    + '.swing.info .sw-k{color:var(--warn,#b54708)}.swing .sw-t{font-weight:700;font-size:15px}'
    + '.swing ul{margin:6px 0;padding-left:18px;font-size:13px}.swing li{margin:2px 0}.swing .sw-do{font-weight:600;margin:6px 0}'
    + '.swing .sw-a{display:flex;flex-wrap:wrap;gap:10px;align-items:center}'
    + '.swing .sw-a button{font:inherit;color:var(--ink,#17202a);background:var(--panel,#fff);border:1px solid var(--line,#e3e7ec);border-radius:6px;padding:6px 10px;cursor:pointer}';
  function style(){
    if(document.getElementById('swingCss')) return;
    const s = document.createElement('style'); s.id = 'swingCss'; s.textContent = CSS; document.head.appendChild(s);
  }
  function show(el, items){
    if(!el) return;
    const list = (items || []).filter(a => a && a.kind === 'SWING' && a.status === 'new');
    if(!list.length){ el.innerHTML = ''; el.hidden = true; return; }
    style();
    el.hidden = false;
    el.innerHTML = list.map(a => {
      const d = a.detail || {}, info = a.severity === 'info';
      return `<div class="swing${info ? ' info' : ''}" role="alert" data-swing="${esc(a.alert_id)}">
        <div class="sw-t"><span class="sw-k">${info ? 'Big change after a release' : 'Big change in one run'}</span>${esc(a.title)}</div>
        ${d.action ? `<div class="sw-do">${esc(d.action)}</div>` : ''}
        <ul>${(Array.isArray(d.lines) ? d.lines : []).map(x => `<li>${esc(x)}</li>`).join('')}</ul>
        <div class="sw-a"><a href="/#data">Open the Data tab</a><button type="button" data-swing-ack="${esc(a.alert_id)}">I checked the files: acknowledge</button></div>
      </div>`;
    }).join('');
    el.querySelectorAll('[data-swing-ack]').forEach(b => b.onclick = async () => {
      if(!window.confirm('Acknowledge only after checking the files read since the run before. The alert then leaves every page.')) return;
      b.disabled = true;
      try {
        const r = await fetch('/api/alerts/' + encodeURIComponent(b.dataset.swingAck) + '/ack',
          {method: 'POST', headers: {'X-Requested-With': 'remitwatch', 'Content-Type': 'application/json'}, body: '{}'});
        if(!r.ok) throw new Error(String(r.status));
      } catch(e){ b.disabled = false; window.alert('Not saved: the site did not answer. Try again in a moment.'); return; }
      if(typeof window.refreshHeader === 'function') window.refreshHeader(); else load(el);
    });
  }
  async function load(el){
    try {
      const r = await fetch('/api/alerts?status=new', {cache: 'no-store'});
      if(!r.ok) return;                 // not signed in yet, or no store: nothing to say
      show(el, (await r.json()).items);
    } catch(e){ /* no answer: the next page load asks again */ }
  }
  window.swingShow = show;
  window.swingLoad = load;
})();
