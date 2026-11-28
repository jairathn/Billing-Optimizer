# Unit ui-signin: progress (plans/briefs/2026-11-28_ui.md, unit ui-signin)

Branch `wip/ui-signin`, worktree `$S/tk/wt/ui-signin` (base: integration tip 9a5ef3f merged with wip/ui-a f1a4877).
Private folder `$S/tk/units/ui-signin` (mode 700); demo port 8776. Invented data only.

## The design (decided before the code)

- One module, `remitwatch/sessions.py`: the session secret, the cookie, the refusal by kind of request, the safe
  `next`. Routes in `remitwatch/api_signin.py` (a router, as api_ops and the others). `api.require_auth` asks the
  cookie first, then the login exactly as today.
- Secret: `REMITWATCH_SESSION_SECRET` (32 characters or more) on either host. Server without it: 32 random bytes made
  once and kept beside the store (`session.key`, mode 600, written whole or not at all). Vercel without it: the site
  keeps today's browser sign-in box (chosen over HKDF from another secret: no coupling to a secret with another job,
  nothing to guess about its strength, one setting the owner adds, /setup offers a value and says what is missing).
- Key: HKDF-SHA256 of the secret, its own label, bound to a SHA-256 of the user name and password, so a password
  change signs every session out on every instance; never derived from the password alone.
- Cookie `__Host-rw_session` = `1.<issued>.<nonce>.<mac>`; HttpOnly, Secure, SameSite=Strict, Path=/, Max-Age 10 h
  (a working day). The expiry is checked from the signed issue time, never from the cookie's Max-Age. Stateless: any
  instance accepts a session another signed.
- Sign out clears this browser's cookie (and a marker so a login the browser cached from the old box is not used
  again by that browser). A copy of the cookie kept elsewhere still works until 10 h after its sign-in, or until the
  password or the secret changes.
- Refusal: a browser's navigation goes to `/signin?next=<same-origin path>` (303); a page's call gets 401 with no
  WWW-Authenticate; a script (no Sec-Fetch headers, no cookie of ours, no page header) gets today's 401 with the
  Basic challenge. In box mode every refusal is today's.
- Basic stays on every route on both hosts; lockouts (`api._blocked`, ten wrong tries in ten minutes per address,
  the same book for the page and the box), timing-safe compares (`api.shared_login`), CSRF (`X-Requested-With` on the
  sign-in and sign-out posts; SameSite=Strict), the owner's lock and the closed-by-default site unchanged.

## Steps

1. Merge ui-a: DONE (7f8262e; the route card's styles moved from index.html's old style block into rw.css).
2. sessions.py, api_signin.py, require_auth: next.
3. The page (static/signin.html, signin.js), session.js (a page's 401 goes to sign-in; Sign out), shell's Sign out.
4. /setup row and generated value; docs (.env.example, SERVER_RUNBOOK, VERCEL_SETUP, OPERATIONS, README).
5. Tests: tests/test_signin.py (both hosts, every route class, flags, expiry, password change, lockout, next, Basic,
   no secret in logs), tests/signin_page.js (Playwright walk, no dialog); the Playwright scripts that used the
   browser's box sign in through the page (same guarantee).
6. Checks (plans/tools/ui/checks.js on /signin at four sizes, both themes), suites in four environments, node checks.

## Numbers measured

- none yet

## Next command

    cd $S/tk/wt/ui-signin && python3 -m pytest -q tests/test_signin.py

## Open questions

- none
