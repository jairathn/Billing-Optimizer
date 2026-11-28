"""
The sign-in page's session: a signed cookie in place of the browser's own sign-in box (unit ui-signin).

A person signs in once on /signin with the dashboard's user name and password: the same check as the browser's box
(api.shared_login: both halves compared every time, timing-safe) behind the same brake on guessing (api._blocked: ten
wrong tries from one address in ten minutes, one book for the page and the box). The answer is a cookie that holds when
it was issued, a random nonce and a MAC, nothing else and nothing secret:

    __Host-rw_session = 1.<issued, unix seconds>.<nonce>.<HMAC-SHA256>      HttpOnly; Secure; SameSite=Strict; Path=/

The MAC's key is derived with HKDF-SHA256 (RFC 5869, its own label) from the session secret, bound to a hash of the
dashboard's user name and password. So a session is good on every instance that has the same settings (Vercel's
instances share no memory and need none: nothing about a session is kept anywhere but in the cookie), and changing the
password, the user name or the secret signs every session out on all of them at once.

The secret. REMITWATCH_SESSION_SECRET (32 characters or more) on either host. A server without it makes 32 random bytes
once and keeps them beside the store (session.key, readable by its owner only, written whole or not at all), so a
restart keeps everyone signed in. A Vercel site without it keeps the browser's own sign-in box, as it was: nothing lasts
there between requests but the settings and the state folder, and a key made from the password alone would let anyone
who copied a cookie test guesses at the password offline. The key is never derived from the password alone.

What a session is good for. Every route a login opens, for SESSION_HOURS (a working day) from its sign-in, checked from
the signed issue time on the server, never from the cookie's own expiry. Sign out clears this browser's cookie. A copy
of the cookie kept elsewhere is still good until those hours run out, or until the password or the secret changes,
which signs every session out at once.

The login stays for scripts: an Authorization header that works today works on every route, on both hosts. A refusal
says what kind of caller it met (refusal()): a browser's navigation goes to the sign-in page and comes back to the same
path after (never anywhere else: safe_next); a call from one of the site's pages gets a 401 with no WWW-Authenticate,
so the browser shows no box and the page sends the person to sign in; anything else (a script) gets today's 401 with the
Basic challenge. While the site has no session secret (box mode), every refusal is today's.

Nothing here writes the password, the secret or a cookie to a log line or an error message.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import os
import re
import secrets
import time
from typing import Any
from urllib.parse import quote

from . import runlog, runtime

COOKIE = "__Host-rw_session"
# set by Sign out: this browser's login from the old sign-in box (a browser keeps one until it is closed) is not used
# again until the person signs in on the page; scripts never carry it
OUT_COOKIE = "__Host-rw_out"
OUT_SECONDS = 30 * 24 * 3600
SESSION_HOURS = 10
SESSION_SECONDS = SESSION_HOURS * 3600
SKEW_SECONDS = 300              # an issue time may run this far ahead of this instance's clock (another instance's)
SECRET_SETTING = "REMITWATCH_SESSION_SECRET"
MIN_SECRET = 32
KEY_FILE = "session.key"
SIGNIN_PATH = "/signin"
_LABEL = b"remitwatch sign-in session cookie v1"
_SALT = b"remitwatch-session-salt-v1"
_TOKEN = re.compile(r"1\.(\d{10})\.([A-Za-z0-9_-]{22})\.([A-Za-z0-9_-]{43})")
_KEY_TEXT = re.compile(r"[0-9a-f]{64}")
_FILE_KEYS: dict[str, bytes] = {}       # key file path -> its secret, once read (a process keeps what it read)

SIGNED_OUT = "Your sign-in has ended. Sign in again."
NOT_SAME_LOGIN = "That user name and password do not match. Check both and try again."


# --------------------------------------------------------------------------------------------- the secret and the key
def key_file(db_path: str) -> str:
    """Where a server keeps the secret it made: beside the store (never in the store, so never in its backups)."""
    return os.path.join(os.path.dirname(os.path.abspath(db_path or "data/remitwatch.sqlite")), KEY_FILE)


def _read_key(path: str) -> bytes | None:
    """The secret in the key file, None when there is no file; ValueError when the file is not one this site wrote."""
    try:
        with open(path, "rb") as fh:
            data = fh.read(4096)
    except FileNotFoundError:
        return None
    text = data.decode("ascii", "replace").strip()
    if not _KEY_TEXT.fullmatch(text):
        raise ValueError("the sign-in key file beside the store is not one this site wrote")
    return bytes.fromhex(text)


def _make_key(path: str) -> bytes:
    """32 random bytes, written whole beside the store or not at all, and never over a key another process made first
    (a hard link fails when the name is taken). Returns the key now in place: this one, or the one that was first."""
    folder = os.path.dirname(path)
    tmp = os.path.join(folder, f".{KEY_FILE}.{secrets.token_hex(6)}.part")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as fh:
            fh.write(secrets.token_bytes(32).hex() + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError:
            pass                        # another process made it a moment before: that one is the key
        except OSError:                 # a file system with no hard links: claim the name, then write it
            try:
                fd2 = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                fd2 = None
            if fd2 is not None:
                with open(tmp, "rb") as src, os.fdopen(fd2, "wb") as dst:
                    dst.write(src.read())
                    dst.flush()
                    os.fsync(dst.fileno())
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
    got = _read_key(path)
    if got is None:
        raise FileNotFoundError("the sign-in key file was not kept")
    return got


def secret(db_path: str | None = None) -> tuple[bytes | None, str | None]:
    """(the session secret, None), or (None, why there is none) in words for /setup. Never a value."""
    raw = os.environ.get(SECRET_SETTING)
    if raw is not None and raw.strip():
        if len(raw.strip()) < MIN_SECRET:
            return None, f"{SECRET_SETTING} is shorter than {MIN_SECRET} characters"
        return raw.encode("utf-8"), None
    if runtime.serverless():
        return None, f"{SECRET_SETTING} is not set"
    path = key_file(db_path or "")
    hit = _FILE_KEYS.get(path)
    if hit is not None:
        return hit, None
    try:
        got = _read_key(path)
        if got is None:
            got = _make_key(path)
    except ValueError as exc:
        runlog.caught(exc, "sessions:key", level="warning")
        return None, f"{KEY_FILE} beside the store is not one this site wrote: delete it and the site makes a new one"
    except OSError as exc:
        runlog.caught(exc, "sessions:key", level="warning")
        return None, f"{KEY_FILE} cannot be kept beside the store ({type(exc).__name__})"
    _FILE_KEYS[path] = got
    return got, None


def setup_line(db_path: str, server: bool, where: str, apply: str) -> tuple[bool | None, str, str, str]:
    """The sign-in page's line on /setup: (ok, label, what is set, how to fix it). ok None: not needed (Vercel keeps the
    browser's box without it). Says whether a server's key file is in place, and never makes one, nor shows a value."""
    label = f"Sign-in page ({SECRET_SETTING}{', optional on a server' if server else ''})"
    fix = (f"Set {SECRET_SETTING} {where} to a long random value ({MIN_SECRET} characters or more, no spaces; the box below "
           f"offers one), {apply}. People then sign in on the site's own page.")
    raw = os.environ.get(SECRET_SETTING) or ""
    if raw.strip():
        if len(raw.strip()) < MIN_SECRET:
            return False, label, (f"set, but shorter than {MIN_SECRET} characters: the site asks for the password in the "
                                  "browser's own sign-in box until it is replaced"), fix
        return True, label, f"set: people sign in on the site's own page, for up to {SESSION_HOURS} hours at a time", fix
    if not server:
        return None, label, "not set: people sign in with the browser's own sign-in box", fix
    path = key_file(db_path)
    try:
        _read_key(path) if os.path.exists(path) else None
    except (OSError, ValueError):
        return False, label, f"not set, and {KEY_FILE} beside the store is not one this site wrote", (
            f"Delete {KEY_FILE} beside the store (everyone signs in again), or set {SECRET_SETTING} {where}, {apply}.")
    if os.path.exists(path):
        return True, label, f"not set: the site made its own key and keeps it beside the store ({KEY_FILE})", fix
    if os.access(os.path.dirname(path) or ".", os.W_OK):
        return True, label, f"not set: the site makes its own key beside the store ({KEY_FILE}) at the first sign-in", fix
    return False, label, "not set, and the folder that holds the store cannot be written: the browser's own box asks", fix


def _hkdf(ikm: bytes, info: bytes, length: int = 32, salt: bytes = _SALT) -> bytes:
    """HKDF-SHA256 (RFC 5869): extract with the salt, expand with the info."""
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    out, block, n = b"", b"", 1
    while len(out) < length:
        block = hmac.new(prk, block + info + bytes([n]), hashlib.sha256).digest()
        out += block
        n += 1
    return out[:length]


def signing_key(secret_bytes: bytes, user: str, password: str) -> bytes:
    """The MAC key: the secret's own key for sessions, bound to this user name and password (a change of either makes a
    different key, so every earlier cookie fails on every instance)."""
    login = hashlib.sha256(b"remitwatch login\x00" + user.encode("utf-8") + b"\x00" + password.encode("utf-8")).digest()
    return _hkdf(secret_bytes, _LABEL + b"\x00" + login)


# --------------------------------------------------------------------------------------------------------- the cookie
def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _mac(key: bytes, body: str) -> str:
    return _b64(hmac.new(key, b"remitwatch-session|" + body.encode("ascii"), hashlib.sha256).digest())


def mint(key: bytes, now: float | None = None) -> str:
    """A new session: its issue time, a random nonce and the MAC over both."""
    iat = int(time.time() if now is None else now)
    body = f"1.{iat:010d}.{_b64(secrets.token_bytes(16))}"
    return f"{body}.{_mac(key, body)}"


def issued(key: bytes, token: str | None, now: float | None = None) -> int | None:
    """The issue time of a good session, or None: a cookie of the wrong shape, a MAC that does not hold (another key: a
    changed password or secret), one issued in the future, or one older than SESSION_HOURS."""
    if not token or len(token) > 200:
        return None
    m = _TOKEN.fullmatch(token)
    if not m:
        return None
    body = token.rsplit(".", 1)[0]
    if not hmac.compare_digest(_mac(key, body).encode("ascii"), m.group(3).encode("ascii")):
        return None
    iat, t = int(m.group(1)), time.time() if now is None else now
    if iat > t + SKEW_SECONDS or t >= iat + SESSION_SECONDS:
        return None
    return iat


def cookie_attrs() -> dict[str, Any]:
    return {"path": "/", "secure": True, "httponly": True, "samesite": "strict"}


def set_session(response: Any, token: str) -> None:
    response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, **cookie_attrs())
    response.delete_cookie(OUT_COOKIE, **cookie_attrs())


def end_session(response: Any) -> None:
    response.delete_cookie(COOKIE, **cookie_attrs())
    response.set_cookie(OUT_COOKIE, "1", max_age=OUT_SECONDS, **cookie_attrs())


# ------------------------------------------------------------------------------------------- what kind of caller this is
def from_browser(request: Any) -> bool:
    """A browser, as far as a request says: browsers send the Sec-Fetch headers (scripts do not), the site's own pages
    send X-Requested-With on a change, a browser that signed in carries the site's cookies, and a browser asks for HTML
    when it navigates."""
    h = request.headers
    if h.get("sec-fetch-mode") or h.get("sec-fetch-site") or h.get("sec-fetch-dest"):
        return True
    if h.get("x-requested-with") == "remitwatch":
        return True
    cookies = request.cookies
    if COOKIE in cookies or OUT_COOKIE in cookies:
        return True
    return "text/html" in (h.get("accept") or "")


def navigation(request: Any) -> bool:
    """A browser opening a page (the address bar, a link, a reload), not a page's own call for data."""
    if request.method not in ("GET", "HEAD"):
        return False
    mode = request.headers.get("sec-fetch-mode")
    if mode:
        return mode == "navigate"
    return "text/html" in (request.headers.get("accept") or "")


def safe_next(raw: Any) -> str:
    """A place on this site to come back to after signing in, or "/": a path that starts with one slash, printable
    plain characters only (no backslash, no space, tab or line break, which a browser may turn into another site's
    address), never the sign-in itself."""
    s = raw if isinstance(raw, str) else ""
    if not s or len(s) > 2000 or not s.startswith("/") or s.startswith("//"):
        return "/"
    if any(not (0x21 <= ord(c) <= 0x7E) for c in s) or "\\" in s:
        return "/"
    path = s.split("#", 1)[0].split("?", 1)[0]
    if path in (SIGNIN_PATH, "/api/signin", "/api/signout"):
        return "/"
    return s


def next_of(request: Any) -> str:
    """The same-origin path and query a refused navigation asked for, as sent (never decoded and rebuilt)."""
    raw = request.scope.get("raw_path") or request.url.path.encode("utf-8")
    try:
        path = raw.decode("ascii")
    except UnicodeDecodeError:
        return "/"
    query = request.scope.get("query_string") or b""
    try:
        q = query.decode("ascii")
    except UnicodeDecodeError:
        q = ""
    return safe_next(path + (f"?{q}" if q else ""))


def signin_url(next_path: str, **flags: str) -> str:
    extra = "".join(f"&{k}={quote(v, safe='')}" for k, v in flags.items())
    return f"{SIGNIN_PATH}?next={quote(safe_next(next_path), safe='')}{extra}"
