"""
The sign-in page and its calls (unit ui-signin; the session itself is remitwatch.sessions).

    GET  /signin        the page: no sign-in needed to see it (it holds no data). On a site still on the browser's own
                        box it is not shown (the box asks); before a password exists, /setup says what is missing.
    POST /api/signin    {"user", "password", "next"}: the dashboard's login, checked as the box checks it
                        (api.shared_login) behind the same brake on guessing (api._blocked, one book for both). The
                        answer sets the session cookie and says where to go back to: a path on this site, never another.
    POST /api/signout   clears this browser's session (and keeps a login its old box remembers from being used again).
    GET  /api/session   how this site signs people in (page, box, open, setup) and whether this browser holds a good
                        session; never anything else.

The two posts need X-Requested-With, which a form on another site cannot send, and the cookie is SameSite=Strict as
well. A refusal never says which half of the login was wrong, and no answer repeats what was typed.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasicCredentials

from . import sessions

router = APIRouter()

# the page holds no data and runs only its own script: nothing else may load, frame it or receive its form
PAGE_CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
            "connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
MAX_BODY = 8192
UNREADABLE = "The request could not be read. Reload the page and try again."
LOCKED = "Too many wrong tries from this address. Wait ten minutes, then try again."


def _api():
    from . import api
    return api


def _mode() -> str:
    """How this site signs people in now: 'setup' (no usable password yet), 'open' (a developer's own machine with no
    password), 'page' (the sign-in page and its session) or 'box' (the browser's own box: no session secret)."""
    api = _api()
    if api.password_problem():
        return "open" if api.open_allowed() and not api.PASSWORD else "setup"
    return "page" if api.session_key() is not None else "box"


@router.get("/signin", include_in_schema=False)
def signin_page(request: Request, next: str = ""):       # noqa: A002  (the page's own name for where to go back to)
    mode, back = _mode(), sessions.safe_next(next)
    if mode == "setup":
        return RedirectResponse("/setup", status_code=303)
    if mode == "open" or (mode == "page" and _api().session_ok(request)):
        return RedirectResponse(back, status_code=303)      # nothing to sign in to, or signed in already
    if mode == "box":
        return RedirectResponse("/", status_code=303)       # the browser's own box asks there
    return FileResponse(_api().STATIC_DIR / "signin.html",
                        headers={"Cache-Control": "no-store", "Content-Security-Policy": PAGE_CSP})


@router.post("/api/signin", include_in_schema=False)
async def signin(request: Request):
    api = _api()
    api.require_xhr(request)
    mode = _mode()
    if mode == "setup":
        raise HTTPException(status_code=503, detail="This site is not set up yet. Open /setup.")
    if mode == "open":
        return {"ok": True, "next": "/"}
    if mode == "box":
        raise HTTPException(status_code=409, detail="This site signs people in with the browser's own sign-in box.")
    who = api._caller(request)
    if api._blocked(who):
        raise HTTPException(status_code=429, detail=LOCKED)
    raw = await request.body()
    if len(raw) > MAX_BODY:
        raise HTTPException(status_code=400, detail=UNREADABLE)
    try:
        body = json.loads(raw.decode("utf-8") or "{}")
    except (UnicodeDecodeError, ValueError):
        raise HTTPException(status_code=400, detail=UNREADABLE) from None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail=UNREADABLE)
    user, password = body.get("user"), body.get("password")
    if not isinstance(user, str) or not isinstance(password, str) or not user or not password:
        raise HTTPException(status_code=400, detail="Type the user name and the password.")
    if not api.shared_login(HTTPBasicCredentials(username=user, password=password)):
        api._blocked(who, record=True)
        raise HTTPException(status_code=401, detail=sessions.NOT_SAME_LOGIN)
    key = api.session_key()
    if key is None:                     # the secret went away between the two looks (a deleted key file): say so
        raise HTTPException(status_code=409, detail="This site signs people in with the browser's own sign-in box.")
    resp = JSONResponse({"ok": True, "next": sessions.safe_next(body.get("next")), "hours": sessions.SESSION_HOURS})
    sessions.set_session(resp, sessions.mint(key))
    return resp


@router.post("/api/signout", include_in_schema=False)
def signout(request: Request):
    _api().require_xhr(request)
    resp = JSONResponse({"ok": True})
    sessions.end_session(resp)
    return resp


@router.get("/api/session", include_in_schema=False)
def session_state(request: Request):
    mode, until = _mode(), None
    if mode == "page":
        key = _api().session_key()
        iat = sessions.issued(key, request.cookies.get(sessions.COOKIE)) if key is not None else None
        if iat is not None:
            until = datetime.fromtimestamp(iat + sessions.SESSION_SECONDS, timezone.utc).isoformat()
    return {"sign_in": mode, "session": until is not None, "until": until, "hours": sessions.SESSION_HOURS}
