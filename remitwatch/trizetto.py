"""
TriZetto direct: the payers' remittances (835 files, .RMT) read straight from TriZetto's SFTP server with a login of
RemitWatch's own, so nobody has to copy them into Dropbox and the lists catch up with payments within hours of the payer
sending them.

    TRIZETTO_SFTP_USER        the login TriZetto issued for RemitWatch (docs/TRIZETTO_REQUEST.md asks for a second SFTP login
                              with its own username and password, not the Site ID's, which ModMed may use)
    TRIZETTO_SFTP_PASSWORD    that login's password (given by phone or in person, never by email)
    TRIZETTO_SFTP_HOST        default sftp.gatewayedi.com
    TRIZETTO_SFTP_PORT        default 22
    TRIZETTO_SFTP_DIRS        the folders to read, comma-separated (default: the login folder, three levels down)
    TRIZETTO_SFTP_HOSTKEY     the server key's fingerprints (SHA256:..., or MD5:...) as TriZetto gives them, one per key type,
                              separated by commas; any of them is accepted. A value that cannot be read stops the feed before
                              it connects. Without it the first key seen is kept and any other is refused (trust on first
                              use): a different key means someone may be standing between the site and TriZetto, and the
                              password is not sent to it. A server has one key per type and offers its favourite first, so
                              when that one is not the key expected, its other types are asked for before giving up.
    TRIZETTO_SFTP_SERVERLESS  1 lets a serverless host (Vercel) read TriZetto too. Off by default: the server reads it. Each
                              site keeps its own hold on a refused password, so only one site should sign in with the login.

Nothing in TriZetto's portal is changed for the feed: not the Remittance Advice Settings ("Auto Generate Remittances"), not
the Site ID's existing SFTP password (docs/TRIZETTO_REQUEST.md, docs/OPERATIONS.md "TriZetto direct"). Read only: nothing
on TriZetto's server is deleted, renamed or written, because the practice's billing system may read the same files, and
TriZetto's text reports (.DAT) are not opened. Each file is read once (its path, size and time are kept in the store); a
remittance read twice under two names is stored once (the store keys remittances by content). Other X12 files found there
(.edi, .x12, .txt) go to the same loaders as Dropbox's, so a claim file (837) is read too. EMA exports are not on TriZetto.

Every SFTP call has a time limit, and a watchdog closes the connection at the pull's deadline, so a server that stops
answering cannot hold a run past its budget. What was read by then is kept.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import posixpath
import re
import secrets
import socket
import stat
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

DEFAULT_HOST = "sftp.gatewayedi.com"
# remittances and X12 files only. TriZetto's own claim report is a text .DAT file, and a download of one starts the next
# report (as clearinghouse guides describe it): reading it could change what the practice's billing system receives.
WANTED = re.compile(r"(?i)\.(rmt|835|era|edi|x12|txt)$")
MAX_FILE_BYTES = 60 * 1024 * 1024
BATCH = 40
# the watchdog closes the connection this many seconds after the pull's deadline if an SFTP call is still waiting then
WATCHDOG_GRACE = 5
# the /setup test's whole exchange with TriZetto (sign-in, folders, one file) ends within this many seconds
CHECK_SECONDS = 90
SERVERLESS_SETTING = "TRIZETTO_SFTP_SERVERLESS"
SEEN_DDL = """
CREATE TABLE IF NOT EXISTS trizetto_seen (path TEXT PRIMARY KEY, size INTEGER, mtime INTEGER, at TEXT, status TEXT);
"""
HOSTKEY_META = "trizetto:hostkey"
# A refused sign-in is not tried again with the same user name and password for a day: TriZetto locks a login for an hour
# after repeated failures, and if the login is the Site ID's own, the practice's billing system (ModMed) uses it for its
# claims and remittances too. A new password is tried at once; a day later the same one is tried once (TriZetto may have
# switched SFTP on in the meantime). Only a salted, slow hash of the settings is kept, never the password.
REFUSED_META = "trizetto:signin_refused"
RETRY_AFTER_HOURS = 24
# what to do about a refused sign-in (docs/TRIZETTO_REQUEST.md: requests to TriZetto in writing, the Site ID's password never
# touched, passwords never by email)
AFTER_REFUSAL = ("Nothing signs in with this password again for a day. If it is the right one, the TriZetto administrator asks "
                 "TriZetto in writing (the portal's Contact Us) to reset the RemitWatch login's password, and gets the new one by "
                 "phone or in person, never by email. Change it yourself only on a page that shows the RemitWatch login's "
                 "username, never the Site ID's.")


class TriZettoError(Exception):
    pass


class SignInRefused(TriZettoError):
    """TriZetto refused the sign-in (not the network, nor the server's key). kind says how: "password" (TriZetto refused the
    password, sent by the password method), "interactive" (refused after the password was typed into its prompt),
    "method" (the server takes no password for this login), or "" (anything else)."""

    def __init__(self, msg: str = "", kind: str = ""):
        super().__init__(msg)
        self.kind = kind


class FileServiceRefused(TriZettoError):
    """TriZetto accepted the user name and password but would not open its file service: the sign-in itself works."""


class SignInUnclear(TriZettoError):
    """The sign-in did not finish (a timeout, or the line dropped) before TriZetto said yes or no. Twice in a row with the same
    user name and password counts as a refusal: a server may hang up instead of saying no, and that must not lock the login."""


class Stalled(TriZettoError):
    """Signed in, then TriZetto stopped answering: a call ran past its time limit, or the watchdog closed the line."""


UNCLEAR_META = "trizetto:signin_unclear"


@dataclass
class Settings:
    user: str = ""
    password: str = ""
    host: str = DEFAULT_HOST
    port: int = 22
    dirs: list[str] = field(default_factory=lambda: ["."])
    hostkey: str = ""

    @classmethod
    def from_env(cls, env: dict | None = None) -> "Settings":
        e = os.environ if env is None else env
        dirs = [d.strip() for d in (e.get("TRIZETTO_SFTP_DIRS") or "").split(",") if d.strip()] or ["."]
        try:
            port = int(e.get("TRIZETTO_SFTP_PORT") or 22)
        except ValueError:
            port = 22
        return cls(user=(e.get("TRIZETTO_SFTP_USER") or "").strip(), password=e.get("TRIZETTO_SFTP_PASSWORD") or "",
                   host=(e.get("TRIZETTO_SFTP_HOST") or DEFAULT_HOST).strip(), port=port, dirs=dirs,
                   hostkey=(e.get("TRIZETTO_SFTP_HOSTKEY") or "").strip())

    def configured(self) -> bool:
        return bool(self.user and self.password)


def here(env: dict | None = None) -> bool:
    """Whether this site reads TriZetto. A server does. A serverless host (Vercel) does only with TRIZETTO_SFTP_SERVERLESS=1:
    each site keeps its own hold on a refused password and its own password reminder, so with the login set on both sites
    TriZetto would get a refused password from each. The setup docs put TriZetto on the server."""
    from . import runtime
    if not runtime.serverless():
        return True
    e = os.environ if env is None else env
    return (e.get(SERVERLESS_SETTING) or "").strip().lower() in ("1", "true", "yes", "on")


def configured(env: dict | None = None) -> bool:
    """The login is set, and this site is the one that reads TriZetto (here())."""
    return Settings.from_env(env).configured() and here(env)


def fingerprint(key) -> str:
    """OpenSSH's form of a host key's fingerprint: SHA256 of the key, base64 without padding."""
    return "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")


def fingerprints_of(key) -> set[str]:
    """Every way a host key's fingerprint may be written down: OpenSSH's SHA256 form, and the older MD5 form (hex pairs)."""
    md5 = hashlib.md5(key.asbytes()).hexdigest()
    return {fingerprint(key), "MD5:" + ":".join(md5[i:i + 2] for i in range(0, 32, 2))}


def key_type(name: str) -> str:
    """The family of a host key or of a host-key algorithm: ED25519, ECDSA, RSA or DSA ("" when not known)."""
    n = (name or "").lower()
    if "ed25519" in n:
        return "ED25519"
    if n.startswith("ecdsa"):
        return "ECDSA"
    if n.startswith(("ssh-rsa", "rsa-sha2")):
        return "RSA"
    if n.startswith("ssh-dss"):
        return "DSA"
    return ""


def pinned(value: str) -> list[str]:
    """The fingerprints given in TRIZETTO_SFTP_HOSTKEY: one, or several (a server has one key per type: ED25519, ECDSA,
    RSA), separated by commas or spaces. A key-type word in front of one (ED25519 SHA256:...) is allowed and ignored;
    an MD5 fingerprint is compared in lower case."""
    out = []
    for word in re.split(r"[\s,;]+", value or ""):
        w = word.strip().strip("()")
        if w.upper().startswith("SHA256:"):
            out.append("SHA256:" + w[7:].rstrip("="))
        elif w.upper().startswith("MD5:"):
            out.append("MD5:" + w[4:].lower())
        elif re.fullmatch(r"(?i)[0-9a-f]{2}(:[0-9a-f]{2}){15}", w):
            out.append("MD5:" + w.lower())
    return out


def hostkey_problem(value: str) -> str:
    """Why TRIZETTO_SFTP_HOSTKEY cannot be used ("" when it is empty or readable). A value nothing can be read from would
    otherwise leave the first key seen trusted, and the password sent to whoever answers."""
    if not (value or "").strip() or pinned(value):
        return ""
    return ("TRIZETTO_SFTP_HOSTKEY is set but holds no fingerprint that can be read, so nothing was sent. Write each one as "
            "TriZetto gives it: SHA256:... (or MD5: and 16 hex pairs), several separated by commas.")


class _NoOtherKeyType(TriZettoError):
    """The server offered no host-key type that was not tried already."""


class Session:
    """One sign-in to TriZetto's SFTP server. known: the fingerprint kept from an earlier sign-in (trust on first use)."""

    def __init__(self, s: Settings, known: str | None = None, timeout: float = 30):
        self.s, self.known, self.timeout = s, known, timeout
        self.transport = self.sftp = None
        self.key_fp = ""
        self.first_key = False
        self.expired = False            # the watchdog closed the connection
        self._watchdog: threading.Timer | None = None

    def arm(self, seconds: float) -> None:
        """Close the connection after `seconds`, whatever call is waiting on TriZetto then: a server that stops answering
        (or a line that goes silent) cannot hold a run past its deadline."""
        self.disarm()
        self._watchdog = threading.Timer(max(0.5, seconds), self._expire)
        self._watchdog.daemon = True
        self._watchdog.start()

    def disarm(self) -> None:
        if self._watchdog is not None:
            self._watchdog.cancel()
            self._watchdog = None

    def _expire(self) -> None:
        self.expired = True
        self._close_lines()

    def _handshake(self, skip: set[str]):
        """Connect and agree keys with the server, asking only for host-key types not in `skip`."""
        import paramiko
        if self.expired:
            raise TriZettoError("The time for TriZetto ran out before the sign-in; nothing was sent. The next run tries again.")
        try:
            sock = socket.create_connection((self.s.host, self.s.port), timeout=self.timeout)
        except OSError as exc:
            raise TriZettoError(f"Could not reach {self.s.host}:{self.s.port} ({type(exc).__name__}). Check the host and that the network allows it.") from exc
        t = paramiko.Transport(sock)
        t.banner_timeout = t.auth_timeout = self.timeout
        self.transport = t
        try:
            if skip:
                opts = t.get_security_options()
                left = tuple(k for k in opts.key_types if (key_type(k) or k) not in skip)
                if not left:
                    raise _NoOtherKeyType("no other key type")
                opts.key_types = left
            t.start_client(timeout=self.timeout)
            # paramiko's start_client returns quietly when its own time runs out before the keys are agreed (a host that
            # takes the connection and never speaks SSH): a session with no keys agreed is no session
            if not t.is_active() or not getattr(t, "initial_kex_done", False):
                raise TriZettoError(f"{self.s.host} did not complete the SSH handshake within {self.timeout:g} s; nothing was sent.")
        except Exception as exc:
            self._close_lines()         # not close(): the watchdog stays armed for the caller
            try:
                sock.close()            # a transport that never started does not close its socket itself
            except OSError:
                pass
            if isinstance(exc, TriZettoError):
                raise
            if isinstance(exc, paramiko.SSHException) and "protocol banner" in str(exc) \
                    and isinstance(exc.__cause__ or exc.__context__, TimeoutError):
                # the same silent host as above, caught by paramiko's own banner clock before start_client's clock (which
                # of the two fires first depends on the machine's load): one situation, one message
                raise TriZettoError(f"{self.s.host} did not complete the SSH handshake within {self.timeout:g} s; nothing was sent.") from exc
            raise TriZettoError(f"{self.s.host} did not complete the SSH handshake ({type(exc).__name__}).") from exc
        return t

    def __enter__(self) -> "Session":
        import paramiko
        if not self.s.configured():         # an empty password is never sent, so it can never be refused
            raise TriZettoError("TRIZETTO_SFTP_USER and TRIZETTO_SFTP_PASSWORD are not both set; nothing was sent.")
        problem = hostkey_problem(self.s.hostkey)
        if problem:
            raise TriZettoError(problem)
        given = pinned(self.s.hostkey)
        want = set(given or ([self.known] if self.known else []))
        # paramiko asks for the server's favourite key type first; when that key is not the one expected, the server's other
        # types are asked for (a new handshake each, no password sent) before the key is called wrong
        tried: set[str] = set()
        wrong = ""
        for _ in range(5):          # ED25519, ECDSA, RSA, DSA: a server has at most one key of each
            try:
                t = self._handshake(tried)
            except TriZettoError:
                if wrong:           # no other key type answered: the key first offered is the answer
                    break
                raise
            key = t.get_remote_server_key()
            if not want or fingerprints_of(key) & want:
                wrong = ""
                break
            wrong = wrong or f"{key.get_name()} {fingerprint(key)}"
            tried.add(key_type(key.get_name()) or key.get_name())
            self._close_lines()
        if wrong:
            self.close()
            raise TriZettoError(f"The server's key is not the one {'set in TRIZETTO_SFTP_HOSTKEY' if given else 'seen before'} "
                                f"({wrong}, expected {' or '.join(sorted(want))}). Nothing was sent to it. If TriZetto "
                                "changed its key, confirm the new fingerprint with TriZetto in writing and set it as TRIZETTO_SFTP_HOSTKEY.")
        self.key_fp = fingerprint(key)
        self.first_key = not want

        def answered() -> bool:
            # paramiko forgets the user name when it reads the server's refusal (USERAUTH_FAILURE): TriZetto did answer, even
            # if it hung up straight after; a line that dropped or timed out before any answer keeps it
            h = getattr(t, "auth_handler", None)
            return h is not None and getattr(h, "username", "") is None
        kind, left = "password", None
        try:
            try:
                left = t.auth_password(self.s.user, self.s.password, fallback=False)
            except paramiko.BadAuthenticationType as bad:
                if "keyboard-interactive" not in (getattr(bad, "allowed_types", None) or []):
                    raise
                # the server asks for the password as a typed answer: give it the same password once (a server asking again
                # gets nothing, so a refused password is never sent twice in one sign-in), judged by the same rule
                kind = "interactive"
                rounds = []

                def answer(title, instructions, prompts):
                    if not prompts:         # a notice with nothing to answer (RFC 4256 allows one): not the password's round
                        return []
                    rounds.append(1)
                    return [self.s.password for _ in prompts] if len(rounds) == 1 else ["" for _ in prompts]
                left = t.auth_interactive(self.s.user, answer)
        except Exception as exc:
            # a refusal is TriZetto's own answer to the user name and password; a sign-in that timed out or a line that dropped
            # before any answer is the network, and says nothing about the password
            no_method = isinstance(exc, paramiko.BadAuthenticationType)
            definite = not self.expired and (no_method or (isinstance(exc, paramiko.AuthenticationException) and (
                answered() or (t.is_active() and not re.search(r"timeout|timed out|EOF|shut down|closed", str(exc), re.I)))))
            self.close()
            if not definite:
                raise SignInUnclear(f"The sign-in to {self.s.host} did not finish ({type(exc).__name__}: {str(exc)[:120]}). "
                                    "Nothing was concluded about the password; the next run tries again.") from exc
            if no_method:
                offered = ", ".join(getattr(exc, "allowed_types", None) or []) or "nothing"
                raise SignInRefused(f"TriZetto's server does not take a password for this login (it offers: {offered}). Ask "
                                    "TriZetto in writing (the portal's Contact Us) for the RemitWatch login to sign in with a password.",
                                    kind="method") from exc
            if kind == "password":
                raise SignInRefused("TriZetto refused the password. Check that TRIZETTO_SFTP_USER and TRIZETTO_SFTP_PASSWORD are the "
                                    "login and password TriZetto gave for RemitWatch.", kind="password") from exc
            raise SignInRefused("TriZetto refused the sign-in (its server asks for the password as a typed answer). Check that "
                                "TRIZETTO_SFTP_USER and TRIZETTO_SFTP_PASSWORD are the login and password TriZetto gave for RemitWatch.",
                                kind="interactive") from exc
        if not t.is_authenticated():
            self.close()
            raise SignInRefused(f"TriZetto did not accept the sign-in: it asks for another step ({', '.join(left or []) or 'not named'}).")
        try:
            # opened here rather than with SFTPClient.from_transport, so that opening it, and every call after, has a time limit
            chan = t.open_session(timeout=self.timeout)
            if chan is None:
                raise TriZettoError("no channel")
            chan.settimeout(self.timeout)
            chan.invoke_subsystem("sftp")
            self.sftp = paramiko.SFTPClient(chan)
        except Exception as exc:        # signed in, but the file service refused (SFTP not switched on, or too many sessions)
            self.close()
            raise FileServiceRefused(f"TriZetto accepted the sign-in but would not open its file service ({type(exc).__name__}). "
                                     "Ask TriZetto in writing (the portal's Contact Us) to switch SFTP on for this login.") from exc
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _close_lines(self) -> None:
        for x in (self.sftp, self.transport):
            try:
                if x is not None:
                    x.close()
            except Exception:
                pass

    def close(self) -> None:
        self.disarm()
        self._close_lines()

    def _stalled(self, exc: BaseException) -> bool:
        """The server stopped answering (a call ran out of time, the watchdog closed the line, or the line dropped), as
        opposed to an answer such as 'no such file'."""
        return (self.expired or isinstance(exc, (socket.timeout, TimeoutError, EOFError))
                or "connection dropped" in str(exc).lower() or not (self.transport is not None and self.transport.is_active()))

    def walk(self, depth: int = 3) -> list[tuple[str, int, int]]:
        """Every file worth reading under the folders set: (path, size, modified time), oldest first."""
        out, seen_dirs = [], set()

        def go(path: str, level: int) -> None:
            if path in seen_dirs or level > depth:
                return
            seen_dirs.add(path)
            try:
                entries = self.sftp.listdir_attr(path)
            except Exception as exc:
                if self._stalled(exc):
                    raise Stalled(f"TriZetto stopped answering while the folder {path!r} was listed ({type(exc).__name__}).") from exc
                if not isinstance(exc, OSError):
                    raise
                if level == 0:
                    raise TriZettoError(f"The folder {path!r} could not be listed ({exc}).") from exc
                return
            for a in entries:
                name = a.filename
                if name in (".", "..") or name.startswith("."):
                    continue
                full = name if path in (".", "") else posixpath.join(path, name)
                if stat.S_ISDIR(a.st_mode or 0):
                    go(full, level + 1)
                elif stat.S_ISREG(a.st_mode or 0) and WANTED.search(name):
                    out.append((full, int(a.st_size or 0), int(a.st_mtime or 0)))
        for d in self.s.dirs:
            go(d, 0)
        return sorted(out, key=lambda f: (f[2], f[0]))

    def read(self, path: str) -> bytes:
        try:
            with self.sftp.open(path, "rb") as fh:
                fh.prefetch()
                data = fh.read(MAX_FILE_BYTES + 1)
        except Exception as exc:
            if self._stalled(exc):
                raise Stalled(f"TriZetto stopped answering while {posixpath.basename(path)} was read ({type(exc).__name__}).") from exc
            raise
        if len(data) > MAX_FILE_BYTES:
            raise TriZettoError(f"{path} is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB")
        return data


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _digest(s: Settings, salt: bytes, how: str) -> str:
    material = f"{s.host}\0{s.port}\0{s.user}\0{s.password}".encode()
    if how == "scrypt":
        return hashlib.scrypt(material, salt=salt, n=2 ** 14, r=8, p=1, dklen=16).hex()
    return hashlib.pbkdf2_hmac("sha256", material, salt, 200_000, dklen=16).hex()


def credential_tag(s: Settings, salt: bytes | None = None) -> str:
    """Which server, user name and password a refusal was for, without the password: a salted, deliberately slow hash
    (scrypt), so a copy of the store (a backup, the dashboard's copy) does not let anyone test password guesses quickly.
    Compare with same_credentials()."""
    salt = salt or secrets.token_bytes(16)
    try:
        return f"scrypt${salt.hex()}${_digest(s, salt, 'scrypt')}"
    except (AttributeError, ValueError):            # a Python built without scrypt
        return f"pbkdf2${salt.hex()}${_digest(s, salt, 'pbkdf2')}"


def same_credentials(tag, s: Settings) -> bool:
    """Whether `tag` (credential_tag) was made from these settings."""
    try:
        how, salt, digest = str(tag or "").split("$")
        if how not in ("scrypt", "pbkdf2"):
            return False
        return hmac.compare_digest(_digest(s, bytes.fromhex(salt), how), digest)
    except (ValueError, AttributeError):
        return False


def _hold_row(conn):
    try:
        return conn.execute("SELECT value FROM meta WHERE key=?", (REFUSED_META,)).fetchone()
    except Exception:
        return None


def refusal(conn, s: Settings | None = None, now: datetime | None = None) -> dict | None:
    """The refused sign-in that still holds for these settings ({at, detail, unclear, kind, retry_at, text}), or None: none was
    refused, a new user name or password was entered since (the refusal is cleared), or the wait is over (one more try is due)."""
    s = settings_or_env(s)
    row = _hold_row(conn)
    if not row:
        return None
    try:
        r = json.loads(row[0])
        at = datetime.fromisoformat(r["at"])
    except (TypeError, ValueError, KeyError):
        r, at = {}, None
    if not same_credentials(r.get("who"), s):
        forget_refusal(conn)
        return None
    hours = 6 if r.get("unclear") else RETRY_AFTER_HOURS
    if at is None or ((now or datetime.now(timezone.utc)) - at).total_seconds() >= hours * 3600:
        return None
    held = {"at": r["at"], "detail": r.get("detail") or "", "unclear": bool(r.get("unclear")), "kind": r.get("kind") or "",
            "retry_at": (at + timedelta(hours=hours)).isoformat(timespec="minutes")}
    held["text"] = held_text(held)
    return held


def _utc_minute(iso) -> str:
    """A time as 'YYYY-MM-DD HH:MM' in UTC, without the offset ('2026-11-23 19:32', not '2026-11-23 19:32+00:00'). The
    words around it say UTC."""
    try:
        d = datetime.fromisoformat(str(iso or ""))
    except ValueError:
        return str(iso or "")[:16].replace("T", " ")
    return (d.astimezone(timezone.utc) if d.tzinfo else d).strftime("%Y-%m-%d %H:%M")


def held_text(held: dict) -> str:
    """What a hold means, in words for the person reading /setup or the run's log."""
    at, until = _utc_minute(held.get("at")), _utc_minute(held.get("retry_at"))
    if held.get("unclear"):
        why = f"The sign-in to TriZetto did not finish twice in a row (last {at} UTC)."
    elif held.get("kind") == "password":
        why = f"TriZetto refused this user name and password on {at} UTC."
    elif held.get("kind") == "method":
        why = f"TriZetto's server would not take a password for this login on {at} UTC."
    else:
        why = f"TriZetto refused the sign-in with this user name and password on {at} UTC."
    return (f"{why} Nothing signs in with them again until {until} UTC, so the login is never locked (TriZetto locks a login "
            "after repeated failures). A new password is tried at once.")


def settings_or_env(s: Settings | None) -> Settings:
    return s or Settings.from_env()


def remember_refusal(conn, s: Settings, detail: str, unclear: bool = False, kind: str = "") -> None:
    """Hold the sign-in: a day after TriZetto's refusal; 6 hours after two sign-ins that did not finish (TriZetto may just
    have been down). kind: SignInRefused.kind, so the data checks only say "refused the password" when it was."""
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                 (REFUSED_META, json.dumps({"who": credential_tag(s), "at": _now(), "detail": detail[:300], "unclear": unclear,
                                            "kind": "" if unclear else (kind or "")})))
    conn.commit()


def forget_refusal(conn) -> bool:
    """End a hold; True when there was one (the store changed, so a serverless run must save it)."""
    try:
        gone = conn.execute("DELETE FROM meta WHERE key=?", (REFUSED_META,)).rowcount
        conn.commit()
        return bool(gone)
    except Exception:
        return False


def _mark(exc: BaseException, **flags) -> None:
    for k, v in flags.items():
        if v:
            try:
                setattr(exc, k, True)
            except Exception:
                pass


def pull(conn, handle: Callable[[list[tuple]], dict], deadline: float, settings: Settings | None = None, log=print) -> dict:
    """Read the files not read yet (or changed since) and hand them to `handle` in batches: [(name, bytes, mtime)] ->
    the loaders' counts. The files read are remembered in the store (trizetto_seen), and so is the server's key. A user
    name and password TriZetto refused are not tried again for a day (REFUSED_META), so the login is never locked.
    The deadline is checked before each file (`remaining` says how many are left for the next run), and a watchdog closes
    the connection at the deadline if TriZetto stops answering: the files read by then are kept, and `error` says so."""
    s = settings or Settings.from_env()
    conn.executescript(SEEN_DDL)
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    out: dict = {"listed": 0, "new": 0, "downloaded": 0, "remits": 0, "ema": 0, "claims_837": 0, "errors": 0, "remaining": 0,
                 "failed": [], "seen_changed": False}
    had_hold = _hold_row(conn) is not None
    held = refusal(conn, s)
    if held:
        log(f"  TriZetto: {held['text']}")
        return dict(out, skipped=held["text"], refused_at=held["at"])
    # a hold for other settings (a new password since) was just ended: the store changed, so a serverless run must save it,
    # or its saved copy and Today would go on saying the password was refused
    cleared = had_hold and _hold_row(conn) is None
    if cleared:
        out["seen_changed"] = out["hold_changed"] = True
    row = conn.execute("SELECT value FROM meta WHERE key=?", (HOSTKEY_META,)).fetchone()
    ses = Session(s, known=row[0] if row else None)
    ses.arm(max(1.0, deadline - time.time()) + WATCHDOG_GRACE)
    try:
        try:
            ses.__enter__()
        except BaseException:
            ses.close()
            raise
    except SignInRefused as exc:
        remember_refusal(conn, s, str(exc), kind=exc.kind)
        _mark(exc, seen_changed=True, hold_changed=True)     # a serverless run saves the store, or the hold would be lost with it
        raise
    except SignInUnclear as exc:
        # a second unclear sign-in in a row with the same user name and password is taken as a refusal: a server may hang up
        # instead of saying no, and the login must not be locked by an hourly retry
        got = conn.execute("SELECT value FROM meta WHERE key=?", (UNCLEAR_META,)).fetchone()
        try:
            prev = json.loads(got[0]) if got else {}
        except (TypeError, ValueError):
            prev = {}
        n = (prev.get("n") or 0) + 1 if same_credentials(prev.get("who"), s) else 1
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                     (UNCLEAR_META, json.dumps({"who": prev["who"] if n > 1 else credential_tag(s), "n": n})))
        conn.commit()
        _mark(exc, seen_changed=True, hold_changed=cleared)
        if n >= 2:
            remember_refusal(conn, s, f"The sign-in did not finish twice in a row ({str(exc)[:160]}); waiting 6 hours so the login is never locked.",
                             unclear=True)
            _mark(exc, hold_changed=True)
        raise
    except FileServiceRefused as exc:   # the sign-in itself worked: no hold
        if forget_refusal(conn) or cleared:
            _mark(exc, seen_changed=True, hold_changed=True)
        conn.execute("DELETE FROM meta WHERE key=?", (UNCLEAR_META,))
        conn.commit()
        raise
    except BaseException as exc:        # the network, the handshake or the server's key: nothing about the password
        _mark(exc, seen_changed=cleared, hold_changed=cleared)
        raise
    conn.execute("DELETE FROM meta WHERE key=?", (UNCLEAR_META,))
    conn.commit()
    if forget_refusal(conn):
        out["seen_changed"] = out["hold_changed"] = True
    items: list[tuple] = []
    rows: list[tuple] = []

    def flush() -> None:
        if items:
            out["downloaded"] += len(items)
            part = handle(list(items)) or {}
            for k in ("remits", "ema", "claims_837"):
                out[k] += part.get(k) or 0
            out["errors"] += part.get("errors") or 0
            out["failed"] += [f"{n} (could not be read)" for n in part.get("failed") or []]
            items.clear()
        if rows:
            conn.executemany("INSERT OR REPLACE INTO trizetto_seen (path, size, mtime, at, status) VALUES (?,?,?,?,?)", rows)
            conn.commit()
            out["seen_changed"] = True
            rows.clear()
    try:
        if ses.first_key:
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (HOSTKEY_META, ses.key_fp))
            conn.commit()
            out["hostkey"] = ses.key_fp
            out["seen_changed"] = True
        try:
            files = ses.walk()
        except TriZettoError as exc:        # signed in, then a folder could not be listed or TriZetto stopped answering
            out["error"] = f"{exc} Nothing was read; the next run tries again."
            return out
        seen = {p: (sz, mt) for p, sz, mt in conn.execute("SELECT path, size, mtime FROM trizetto_seen")}
        todo = [f for f in files if seen.get(f[0]) != (f[1], f[2])]
        out["listed"], out["new"] = len(files), len(todo)
        if files:
            out["newest_file"] = datetime.fromtimestamp(files[-1][2], timezone.utc).isoformat(timespec="minutes")
        done = 0
        for i, (path, size, mtime) in enumerate(todo):
            if time.time() > deadline:
                out["remaining"] = len(todo) - i
                break
            if size > MAX_FILE_BYTES:
                rows.append((path, size, mtime, _now(), "too large"))
                out["errors"] += 1
                out["failed"].append(f"{posixpath.basename(path)} (too large)")
                continue
            try:
                data = ses.read(path)
            except Stalled as exc:
                # not a reason to stop the run: what was read is kept, the rest is read by the next run (counted, so a
                # serverless cycle carries on with them as it does after its deadline)
                out["error"] = f"{exc} {done} of {len(todo)} new files were read; the rest are read on the next run."
                out["remaining"] = len(todo) - i
                break
            except Exception as exc:
                log(f"  TriZetto: could not read {path}: {exc}")
                out["errors"] += 1         # not remembered: tried again on the next run
                continue
            done += 1
            items.append((posixpath.basename(path), data, mtime or None))
            rows.append((path, size, mtime, _now(), "read"))
            if len(items) >= BATCH:
                flush()
        flush()
    finally:
        ses.close()
    out["failed"] = out["failed"][:20]
    return out


def check(env: dict | None = None, known: str | None = None, conn=None, override: bool = False) -> dict:
    """Every step of the TriZetto connection, for /setup: settings, the server and its key, the sign-in, the files there,
    and one remittance read. Only file names, counts and dates come back. conn: the store that holds the sign-in hold;
    while a hold is in place the test does not sign in (that would send a refused password again), unless override."""
    s = Settings.from_env(env)
    steps: list[dict] = []
    if not s.configured():
        steps.append({"step": "Settings", "ok": False,
                      "detail": "set TRIZETTO_SFTP_USER (the login TriZetto issued for RemitWatch) and TRIZETTO_SFTP_PASSWORD"})
        return {"ok": False, "steps": steps}
    if not here(env):
        steps.append({"step": "Settings", "ok": False, "detail": "This site runs on a serverless host (Vercel), which does not read "
                      "TriZetto: the server does, and its /setup has this test. Only if no server reads TriZetto with this login, set "
                      f"{SERVERLESS_SETTING}=1 here and redeploy. Otherwise remove the TRIZETTO_SFTP_ settings from this site."})
        return {"ok": False, "steps": steps, "not_here": True}
    problem = hostkey_problem(s.hostkey)
    if problem:
        steps.append({"step": "Settings", "ok": False, "detail": problem})
        return {"ok": False, "steps": steps}
    steps.append({"step": "Settings", "ok": True, "detail": f"login {s.user} on {s.host}:{s.port}, folders {', '.join(s.dirs)}"})
    if conn is not None and not override:
        held = refusal(conn, s)
        if held:
            steps.append({"step": "Sign-in", "ok": False, "detail": "Not tried now. " + held["text"]})
            return {"ok": False, "steps": steps, "held": held}
    ses = Session(s, known=known)
    ses.arm(CHECK_SECONDS)
    try:
        try:
            ses.__enter__()
        except FileServiceRefused as exc:
            steps.append({"step": "Sign-in", "ok": True, "detail": "TriZetto accepted the login and password"})
            steps.append({"step": "Files", "ok": False, "detail": str(exc)})
            return {"ok": False, "steps": steps}
        steps.append({"step": "Server", "ok": True, "detail": f"{s.host} answered; its key is {ses.key_fp}"
                      + (" (kept from now on)" if ses.first_key else " (as expected)")})
        steps.append({"step": "Sign-in", "ok": True, "detail": "TriZetto accepted the login and password"})
        try:
            files = ses.walk()
        except Exception as exc:        # signed in, then the line dropped or a folder could not be listed
            steps.append({"step": "Files", "ok": False, "detail": f"the folders could not be listed ({type(exc).__name__}: {str(exc)[:120]})"})
            return {"ok": False, "steps": steps, "hostkey": ses.key_fp}
        remits = [f for f in files if re.search(r"(?i)\.(rmt|835|era)$", f[0])]
        if not files:
            steps.append({"step": "Files", "ok": False, "detail": "no remittance files in the folders read yet. Check that "
                          "TRIZETTO_SFTP_DIRS is the folder TriZetto named in writing for the RemitWatch login's 835 copies, or "
                          "ask TriZetto in writing (the portal's Contact Us) which folder they go to. Do not change the "
                          "Remittance Advice Settings."})
            return {"ok": False, "steps": steps, "hostkey": ses.key_fp}
        newest = files[-1]
        steps.append({"step": "Files", "ok": True, "detail": f"{len(files)} files ({len(remits)} remittances); newest {posixpath.basename(newest[0])} "
                      f"of {datetime.fromtimestamp(newest[2], timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC"})
        pick = (remits or files)[-1][0]
        try:
            head = ses.read(pick)[:3]
        except Exception as exc:        # the file moved, or the line dropped, between the listing and the read
            steps.append({"step": "Read", "ok": False, "detail": f"{posixpath.basename(pick)} could not be read ({type(exc).__name__})"})
            return {"ok": False, "steps": steps, "hostkey": ses.key_fp}
        steps.append({"step": "Read", "ok": head == b"ISA", "detail": f"{posixpath.basename(pick)} is an X12 file" if head == b"ISA"
                      else f"{posixpath.basename(pick)} does not start like an X12 file"})
        return {"ok": all(x["ok"] for x in steps), "steps": steps, "hostkey": ses.key_fp}
    except SignInRefused as exc:
        steps.append({"step": "Sign-in", "ok": False, "detail": str(exc) + ("" if exc.kind == "method" else " " + AFTER_REFUSAL)})
        return {"ok": False, "steps": steps, "refused": str(exc), "refused_kind": exc.kind}
    except TriZettoError as exc:
        steps.append({"step": "Connection", "ok": False, "detail": str(exc)})
        return {"ok": False, "steps": steps}
    except Exception as exc:        # anything else the SSH library raises is still a step that failed, never a page error
        steps.append({"step": "Connection", "ok": False, "detail": f"the connection failed ({type(exc).__name__}: {str(exc)[:160]})"})
        return {"ok": False, "steps": steps}
    finally:
        ses.close()
