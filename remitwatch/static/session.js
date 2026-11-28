// The sign-in on every page of the app (the dashboard, Upload, Setup): loaded first, before the page's own scripts.
// - A call of this page's that the site refuses for want of a sign-in (401 with no WWW-Authenticate: remitwatch/
//   sessions.py, the session ran out or the password changed) sends the person to the sign-in page, which brings them
//   back to this page, at the same place, after. A 401 with the browser's Basic challenge is left to the browser, as
//   before (a site that still uses the browser's own sign-in box).
// - Sign out (the button in the sidebar's foot, static/shell.js) ends this browser's session.
// Nothing here reads or keeps the password or the cookie (the cookie is HttpOnly: no script can read it).
(function(){
  'use strict';
  if(window.RWSession) return;
  var origFetch = window.fetch, leaving = false;
  var OWN = /^\/api\/(signin|signout|session)$/;

  function toSignin(){
    if(leaving) return; leaving = true;
    var here = location.pathname + location.search + location.hash;
    location.assign('/signin?ended=1&next=' + encodeURIComponent(here));
  }
  // a refusal for want of a sign-in, from this site, not from the sign-in's own calls
  function signedOut(status, url, challenge){
    if(status !== 401 || challenge) return false;
    try { var u = new URL(url, location.href); return u.origin === location.origin && !OWN.test(u.pathname); } catch(e){ return false; }
  }
  if(typeof origFetch === 'function'){
    window.fetch = function(input, init){
      return origFetch.apply(this, arguments).then(function(r){
        try {
          var url = r.url || (typeof input === 'string' ? input : (input && input.url) || '');
          if(signedOut(r.status, url, r.headers.get('www-authenticate'))) toSignin();
        } catch(e){}
        return r;
      });
    };
  }
  // the Upload page sends files with XMLHttpRequest
  if(typeof XMLHttpRequest === 'function'){
    var origSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.send = function(){
      var x = this;
      try { x.addEventListener('load', function(){
        try { if(signedOut(x.status, x.responseURL || location.href, x.getResponseHeader('www-authenticate'))) toSignin(); } catch(e){}
      }); } catch(e){}
      return origSend.apply(this, arguments);
    };
  }

  function signOut(){
    var go = function(){ location.replace('/signin?out=1'); };
    (origFetch || window.fetch)('/api/signout', {method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: {'X-Requested-With': 'remitwatch'}}).then(go, go);
  }
  // Sign out shows where the site signs people in on its own page (not on a site that still uses the browser's box,
  // where only closing the browser signs out)
  function bind(){
    var b = document.getElementById('signOutBtn'); if(!b || b.dataset.bound) return; b.dataset.bound = '1';
    b.addEventListener('click', signOut);
    (origFetch || window.fetch)('/api/session', {credentials: 'same-origin', cache: 'no-store'})
      .then(function(r){ return r.ok ? r.json() : null; })
      .then(function(j){ if(j && j.sign_in === 'page') b.hidden = false; }).catch(function(){});
  }
  if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind); else bind();
  window.RWSession = {signOut: signOut, bind: bind};
})();
