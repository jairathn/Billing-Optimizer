// The sign-in page (remitwatch/api_signin.py, remitwatch/sessions.py). Loaded in the page's head: it sets the theme a
// person chose on this computer before the first paint, then binds the form once the page is read.
// It sends the user name and password to /api/signin and nowhere else, keeps neither (no storage, no address), and
// goes back to the place on this site the person asked for: a path on this site only (safeNext, the server's own rule
// in sessions.safe_next), with the part after # the browser kept through the redirect.
(function(){
  'use strict';
  try { var t = localStorage.getItem('rw:theme'); if(t === 'light' || t === 'dark') document.documentElement.setAttribute('data-theme', t); } catch(e){}

  var SAME_SITE = {credentials: 'same-origin', cache: 'no-store'};
  var WORDS = {
    wrong: 'That user name and password do not match. Check both and try again.',
    empty: 'Type the user name and the password.',
    offline: 'The site did not answer. Check the connection, then try again.',
    kept: 'This browser did not keep the sign-in. Open the site at its https:// address and allow cookies for it, then try again.',
    out: 'You are signed out.',
    ended: 'Your sign-in has ended. Sign in again to carry on where you were.'
  };

  // a place on this site: one leading slash, printable plain characters, no backslash, never the sign-in itself
  function safeNext(s){
    if(typeof s !== 'string' || !s || s.length > 2000 || s.charAt(0) !== '/' || s.charAt(1) === '/') return '/';
    for(var i = 0; i < s.length; i++){ var c = s.charCodeAt(i); if(c < 0x21 || c > 0x7e || c === 0x5c) return '/'; }
    var path = s.split('#')[0].split('?')[0];
    return (path === '/signin' || path === '/api/signin' || path === '/api/signout') ? '/' : s;
  }
  function target(next){
    var n = safeNext(next), h = location.hash || '';
    return (h.length > 1 && n.indexOf('#') < 0 && safeNext('/' + h) !== '/') ? n + h : n;
  }
  function show(el, text){ if(!el) return; el.textContent = text || ''; el.hidden = !text; }

  function bind(){
    var form = document.getElementById('siForm'); if(!form || form.dataset.bound) return; form.dataset.bound = '1';
    var user = document.getElementById('siUser'), pass = document.getElementById('siPass'), go = document.getElementById('siGo');
    var err = document.getElementById('siErr'), note = document.getElementById('siNote');
    var q = new URLSearchParams(location.search), next = safeNext(q.get('next') || '/');
    if(q.get('out')) show(note, WORDS.out); else if(q.get('ended')) show(note, WORDS.ended);
    // a link from another site arrives without the cookie (SameSite=Strict keeps it home): a browser that is signed in
    // already goes straight back
    fetch('/api/session', SAME_SITE).then(function(r){ return r.ok ? r.json() : null; })
      .then(function(j){ if(j && j.session) location.replace(target(next)); }).catch(function(){});
    if(user && !user.value) user.focus(); else if(pass) pass.focus();
    function wrong(text, both){
      show(err, text); show(note, '');
      [user, pass].forEach(function(f){ if(f) f.setAttribute('aria-invalid', both ? 'true' : 'false'); });
      if(pass){ pass.focus(); pass.select(); }
    }
    form.addEventListener('submit', function(e){
      e.preventDefault();
      if(go.getAttribute('aria-busy') === 'true') return;
      var u = user.value, p = pass.value;
      if(!u || !p){ wrong(WORDS.empty, false); if(!u) user.focus(); return; }
      go.setAttribute('aria-busy', 'true'); go.disabled = true; show(err, '');
      var done = function(){ go.removeAttribute('aria-busy'); go.disabled = false; };
      fetch('/api/signin', {method: 'POST', credentials: 'same-origin', cache: 'no-store',
        headers: {'Content-Type': 'application/json', 'X-Requested-With': 'remitwatch'},
        body: JSON.stringify({user: u, password: p, next: next})})
        .then(function(r){ return r.json().catch(function(){ return {}; }).then(function(j){ return {status: r.status, j: j || {}}; }); })
        .then(function(res){
          if(res.status === 200 && res.j.ok){
            pass.value = '';
            // the cookie must have been kept (a plain http:// address on a network, or cookies refused, would not)
            return fetch('/api/session', SAME_SITE).then(function(r){ return r.ok ? r.json() : {}; }).then(function(s){
              if(s && (s.session || s.sign_in === 'open')) location.replace(target(res.j.next || next));
              else { done(); wrong(WORDS.kept, false); }
            });
          }
          done();
          if(res.status === 503){ location.replace('/setup'); return; }
          if(res.status === 409){ location.replace(target(next)); return; }      // the site uses the browser's own box
          if(res.status === 401){ wrong(WORDS.wrong, true); return; }
          wrong(typeof res.j.detail === 'string' && res.j.detail ? res.j.detail : WORDS.wrong, false);
        })
        .catch(function(){ done(); wrong(WORDS.offline, false); });
    });
  }
  if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind); else bind();
  window.RWSignin = {safeNext: safeNext};
})();
