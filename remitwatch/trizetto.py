"""
TriZetto direct: the payers' remittances (835 files, .RMT) read straight from the practice's TriZetto SFTP account, so
nobody has to copy them into Dropbox and the lists catch up with payments within hours of the payer sending them.

    TRIZETTO_SFTP_USER        the TriZetto Site ID (TriZetto support turns SFTP on for it, usually within two days)
    TRIZETTO_SFTP_PASSWORD    the SFTP password TriZetto issues for it
    TRIZETTO_SFTP_HOST        default sftp.gatewayedi.com
    TRIZETTO_SFTP_PORT        default 22
    TRIZETTO_SFTP_DIRS        the folders to read, comma-separated (default: the login folder, three levels down)
    TRIZETTO_SFTP_HOSTKEY     the server key's fingerprint (SHA256:..., or MD5:...), if TriZetto gives one; several (one per
                              key type) separated by commas, and any of them is accepted. Without it the first key
                              seen is kept and any other is refused (trust on first use): a different key means someone
                              may be standing between the site and TriZetto, and the password is not sent to it.

Remittances reach the SFTP folder when "Auto Generate Remittances" is on in TriZetto's portal; change that setting only
after TriZetto confirms how ModMed collects the practice's remittances (docs/OPERATIONS.md, "TriZetto direct"). Read
only: nothing on TriZetto's server is deleted, renamed or written, because the practice's billing system may read the same
files, and TriZetto's text reports (.DAT) are not opened. Each file is read once (its path, size and time are kept in the store); a remittance read twice under
two names is stored once (the store keys remittances by content). Claim files (837) and EMA exports found there are read
the same way as from Dropbox.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import posixpath
import re
import socket
import stat
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
SEEN_DDL = """
CREATE TABLE IF NOT EXISTS trizetto_seen (path TEXT PRIMARY KEY, size INTEGER, mtime INTEGER, at TEXT, status TEXT);
"""
HOSTKEY_META = "trizetto:hostkey"
# A refused sign-in is not tried again with the same user name and password for a day: TriZetto locks a login for an hour
# after repeated failures, and the login may be the Site ID's own, which the practice's billing system (ModMed) uses for its
# claims and remittances. A new password is tried at once; a day later the same one is tried once (TriZetto may have turned
# SFTP on in the meantime). Only a hash of the settings is kept, never the password.
REFUSED_META = "trizetto:signin_refused"
RETRY_AFTER_HOURS = 24


class TriZettoError(Exception):
    pass


class SignInRefused(TriZettoError):
    """TriZetto refused the user name and password (not the network, nor the server's key)."""


class FileServiceRefused(TriZettoError):
    """TriZetto accepted the user name and password but would not open its file service: the sign-in itself works."""


class SignInUnclear(TriZettoError):
    """The sign-in did not finish (a timeout, or the line dropped) before TriZetto said yes or no. Twice in a row with the same
    user name and password counts as a refusal: a server may hang up instead of saying no, and that must not lock the login."""


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


def configured(env: dict | None = None) -> bool:
    return Settings.from_env(env).configured()


def fingerprint(key) -> str:
    """OpenSSH's form of a host key's fingerprint: SHA256 of the key, base64 without padding."""
    return "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")


def fingerprints_of(key) -> set[str]:
    """Every way a host key's fingerprint may be written down: OpenSSH's SHA256 form, and the older MD5 form (hex pairs)."""
    md5 = hashlib.md5(key.asbytes()).hexdigest()
    return {fingerprint(key), "MD5:" + ":".join(md5[i:i + 2] for i in range(0, 32, 2))}


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


class Session:
    """One sign-in to TriZetto's SFTP server. known: the fingerprint kept from an earlier sign-in (trust on first use)."""

    def __init__(self, s: Settings, known: str | None = None, timeout: float = 30):
        self.s, self.known, self.timeout = s, known, timeout
        self.transport = self.sftp = None
        self.key_fp = ""
        self.first_key = False

    def __enter__(self) -> "Session":
        import paramiko
        try:
            sock = socket.create_connection((self.s.host, self.s.port), timeout=self.timeout)
        except OSError as exc:
            raise TriZettoError(f"Could not reach {self.s.host}:{self.s.port} ({type(exc).__name__}). Check the host and that the network allows it.") from exc
        t = paramiko.Transport(sock)
        t.banner_timeout = t.auth_timeout = self.timeout
        self.transport = t
        try:
            t.start_client(timeout=self.timeout)
        except Exception as exc:
            self.close()
            raise TriZettoError(f"{self.s.host} did not complete the SSH handshake ({type(exc).__name__}).") from exc
        key = t.get_remote_server_key()
        self.key_fp = fingerprint(key)
        given = pinned(self.s.hostkey)
        want = given or ([self.known] if self.known else [])
        if want and not (fingerprints_of(key) & set(want)):
            self.close()
            raise TriZettoError(f"The server's key is not the one {'set in TRIZETTO_SFTP_HOSTKEY' if given else 'seen before'} "
                                f"({key.get_name()} {self.key_fp}, expected {' or '.join(want)}). Nothing was sent to it. If TriZetto "
                                "changed its key, confirm the new fingerprint with TriZetto and set it as TRIZETTO_SFTP_HOSTKEY.")
        self.first_key = not want
        def answered() -> bool:
            # paramiko forgets the user name when it reads the server's refusal (USERAUTH_FAILURE): TriZetto did answer, even
            # if it hung up straight after; a line that dropped or timed out before any answer keeps it
            h = getattr(t, "auth_handler", None)
            return h is not None and getattr(h, "username", "") is None
        try:
            try:
                t.auth_password(self.s.user, self.s.password, fallback=False)
            except paramiko.BadAuthenticationType as bad:
                if "keyboard-interactive" not in (getattr(bad, "allowed_types", None) or []):
                    raise
                # the server asks for the password as a typed answer: give it the same password once (a server asking again
                # gets nothing, so a refused password is never sent twice in one sign-in), judged by the same rule
                rounds = []

                def answer(title, instructions, prompts):
                    rounds.append(1)
                    return [self.s.password for _ in prompts] if len(rounds) == 1 else ["" for _ in prompts]
                t.auth_interactive(self.s.user, answer)
        except Exception as exc:
            # a refusal is TriZetto's own answer to the user name and password; a sign-in that timed out or a line that dropped
            # before any answer is the network, and says nothing about the password
            no_method = isinstance(exc, paramiko.BadAuthenticationType)
            definite = no_method or (isinstance(exc, paramiko.AuthenticationException) and (
                answered() or (t.is_active() and not re.search(r"timeout|timed out|EOF|shut down|closed", str(exc), re.I))))
            self.close()
            if not definite:
                raise SignInUnclear(f"The sign-in to {self.s.host} did not finish ({type(exc).__name__}: {str(exc)[:120]}). "
                                    "Nothing was concluded about the password; the next run tries again.") from exc
            raise SignInRefused("TriZetto refused the sign-in: check TRIZETTO_SFTP_USER (the Site ID) and TRIZETTO_SFTP_PASSWORD, and that "
                                "TriZetto support has turned SFTP on for the Site ID.") from exc
        if not t.is_authenticated():
            self.close()
            raise SignInRefused("TriZetto did not accept the sign-in.")
        try:
            self.sftp = paramiko.SFTPClient.from_transport(t)
        except Exception as exc:        # signed in, but the file service refused (SFTP not switched on, or too many sessions)
            self.close()
            raise FileServiceRefused(f"TriZetto accepted the sign-in but would not open its file service ({type(exc).__name__}). "
                                     "Ask TriZetto support to switch SFTP on for this login.") from exc
        if self.sftp is None:
            self.close()
            raise FileServiceRefused("TriZetto accepted the sign-in but would not open its file service. Ask TriZetto support to switch SFTP on.")
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        for x in (self.sftp, self.transport):
            try:
                if x is not None:
                    x.close()
            except Exception:
                pass

    def walk(self, depth: int = 3) -> list[tuple[str, int, int]]:
        """Every file worth reading under the folders set: (path, size, modified time), oldest first."""
        out, seen_dirs = [], set()

        def go(path: str, level: int) -> None:
            if path in seen_dirs or level > depth:
                return
            seen_dirs.add(path)
            try:
                entries = self.sftp.listdir_attr(path)
            except OSError as exc:
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
        with self.sftp.open(path, "rb") as fh:
            fh.prefetch()
            data = fh.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise TriZettoError(f"{path} is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB")
        return data


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def credential_tag(s: Settings) -> str:
    """Which server, user name and password a refusal was for: a hash, so the password itself is never stored."""
    return hashlib.sha256(f"{s.host}\0{s.port}\0{s.user}\0{s.password}".encode()).hexdigest()[:16]


def refusal(conn, s: Settings | None = None, now: datetime | None = None) -> dict | None:
    """The refused sign-in that still holds for these settings ({at, detail, retry_at}), or None: none was refused, a new
    user name or password was entered since (the refusal is cleared), or a day has passed (one more try is due)."""
    s = settings_or_env(s)
    try:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (REFUSED_META,)).fetchone()
    except Exception:
        return None
    if not row:
        return None
    try:
        r = json.loads(row[0])
        at = datetime.fromisoformat(r["at"])
    except (TypeError, ValueError, KeyError):
        r, at = {}, None
    if r.get("who") != credential_tag(s):
        forget_refusal(conn)
        return None
    hours = 6 if r.get("unclear") else RETRY_AFTER_HOURS
    if at is None or ((now or datetime.now(timezone.utc)) - at).total_seconds() >= hours * 3600:
        return None
    return {"at": r["at"], "detail": r.get("detail") or "", "unclear": bool(r.get("unclear")),
            "retry_at": (at + timedelta(hours=hours)).isoformat(timespec="minutes")}


def settings_or_env(s: Settings | None) -> Settings:
    return s or Settings.from_env()


def remember_refusal(conn, s: Settings, detail: str, unclear: bool = False) -> None:
    """Hold the sign-in: a day after TriZetto's refusal; 6 hours after two sign-ins that did not finish (TriZetto may just
    have been down)."""
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                 (REFUSED_META, json.dumps({"who": credential_tag(s), "at": _now(), "detail": detail[:300], "unclear": unclear})))
    conn.commit()


def forget_refusal(conn) -> bool:
    """End a hold; True when there was one (the store changed, so a serverless run must save it)."""
    try:
        gone = conn.execute("DELETE FROM meta WHERE key=?", (REFUSED_META,)).rowcount
        conn.commit()
        return bool(gone)
    except Exception:
        return False


def pull(conn, handle: Callable[[list[tuple]], dict], deadline: float, settings: Settings | None = None, log=print) -> dict:
    """Read the files not read yet (or changed since) and hand them to `handle` in batches: [(name, bytes, mtime)] ->
    the loaders' counts. The files read are remembered in the store (trizetto_seen), and so is the server's key. A user
    name and password TriZetto refused are not tried again for a day (REFUSED_META), so the login is never locked."""
    s = settings or Settings.from_env()
    conn.executescript(SEEN_DDL)
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    out: dict = {"listed": 0, "new": 0, "downloaded": 0, "remits": 0, "ema": 0, "claims_837": 0, "errors": 0, "remaining": 0,
                 "failed": [], "seen_changed": False}
    held = refusal(conn, s)
    if held:
        msg = ((f"The sign-in to TriZetto did not finish twice in a row (last {held['at'][:16].replace('T', ' ')} UTC). " if held.get("unclear") else
                f"TriZetto refused this user name and password on {held['at'][:16].replace('T', ' ')} UTC. ")
               + f"Not signing in again with them until {held['retry_at'].replace('T', ' ')} UTC, so the login is never locked (ModMed may "
               "use it too). A new password is tried at once.")
        log(f"  TriZetto: {msg}")
        return dict(out, skipped=msg, refused_at=held["at"])
    row = conn.execute("SELECT value FROM meta WHERE key=?", (HOSTKEY_META,)).fetchone()
    try:
        ses = Session(s, known=row[0] if row else None).__enter__()
    except SignInRefused as exc:
        remember_refusal(conn, s, str(exc))
        exc.seen_changed = exc.hold_changed = True     # a serverless run saves the store, or the hold would be lost with it
        raise
    except SignInUnclear as exc:
        # a second unclear sign-in in a row with the same user name and password is taken as a refusal: a server may hang up
        # instead of saying no, and the login must not be locked by an hourly retry
        got = conn.execute("SELECT value FROM meta WHERE key=?", (UNCLEAR_META,)).fetchone()
        try:
            prev = json.loads(got[0]) if got else {}
        except (TypeError, ValueError):
            prev = {}
        n = (prev.get("n") or 0) + 1 if prev.get("who") == credential_tag(s) else 1
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (UNCLEAR_META, json.dumps({"who": credential_tag(s), "n": n})))
        conn.commit()
        exc.seen_changed = True
        if n >= 2:
            remember_refusal(conn, s, f"The sign-in did not finish twice in a row ({str(exc)[:160]}); waiting 6 hours so the login is never locked.",
                             unclear=True)
            exc.hold_changed = True
        raise
    except FileServiceRefused as exc:   # the sign-in itself worked: no hold
        if forget_refusal(conn):
            exc.seen_changed = exc.hold_changed = True
        conn.execute("DELETE FROM meta WHERE key=?", (UNCLEAR_META,))
        conn.commit()
        raise
    conn.execute("DELETE FROM meta WHERE key=?", (UNCLEAR_META,))
    conn.commit()
    if forget_refusal(conn):
        out["seen_changed"] = out["hold_changed"] = True
    try:
        if ses.first_key:
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (HOSTKEY_META, ses.key_fp))
            conn.commit()
            out["hostkey"] = ses.key_fp
            out["seen_changed"] = True
        files = ses.walk()
        seen = {p: (sz, mt) for p, sz, mt in conn.execute("SELECT path, size, mtime FROM trizetto_seen")}
        todo = [f for f in files if seen.get(f[0]) != (f[1], f[2])]
        out["listed"], out["new"] = len(files), len(todo)
        if files:
            out["newest_file"] = datetime.fromtimestamp(files[-1][2], timezone.utc).isoformat(timespec="minutes")
        for i in range(0, len(todo), BATCH):
            if time.time() > deadline:
                out["remaining"] = len(todo) - i
                break
            items, rows = [], []
            for path, size, mtime in todo[i:i + BATCH]:
                if size > MAX_FILE_BYTES:
                    rows.append((path, size, mtime, _now(), "too large"))
                    out["errors"] += 1
                    out["failed"].append(f"{posixpath.basename(path)} (too large)")
                    continue
                try:
                    data = ses.read(path)
                except Exception as exc:
                    log(f"  TriZetto: could not read {path}: {exc}")
                    out["errors"] += 1         # not remembered: tried again on the next run
                    continue
                items.append((posixpath.basename(path), data, mtime or None))
                rows.append((path, size, mtime, _now(), "read"))
            out["downloaded"] += len(items)
            if items:
                part = handle(items) or {}
                for k in ("remits", "ema", "claims_837"):
                    out[k] += part.get(k) or 0
                out["errors"] += part.get("errors") or 0
                out["failed"] += [f"{n} (could not be read)" for n in part.get("failed") or []]
            conn.executemany("INSERT OR REPLACE INTO trizetto_seen (path, size, mtime, at, status) VALUES (?,?,?,?,?)", rows)
            conn.commit()
            out["seen_changed"] = out["seen_changed"] or bool(rows)
    finally:
        ses.close()
    out["failed"] = out["failed"][:20]
    return out


def check(env: dict | None = None, known: str | None = None) -> dict:
    """Every step of the TriZetto connection, for /setup: settings, the server and its key, the sign-in, the files there,
    and one remittance read. Only file names, counts and dates come back."""
    s = Settings.from_env(env)
    steps: list[dict] = []
    if not s.configured():
        steps.append({"step": "Settings", "ok": False, "detail": "set TRIZETTO_SFTP_USER (the Site ID) and TRIZETTO_SFTP_PASSWORD"})
        return {"ok": False, "steps": steps}
    steps.append({"step": "Settings", "ok": True, "detail": f"Site ID {s.user} on {s.host}:{s.port}, folders {', '.join(s.dirs)}"})
    try:
        try:
            ses = Session(s, known=known).__enter__()
        except FileServiceRefused as exc:
            steps.append({"step": "Sign-in", "ok": True, "detail": "TriZetto accepted the Site ID and password"})
            steps.append({"step": "Files", "ok": False, "detail": str(exc)})
            return {"ok": False, "steps": steps}
        try:
            steps.append({"step": "Server", "ok": True, "detail": f"{s.host} answered; its key is {ses.key_fp}"
                          + (" (kept from now on)" if ses.first_key else " (as expected)")})
            steps.append({"step": "Sign-in", "ok": True, "detail": "TriZetto accepted the Site ID and password"})
            try:
                files = ses.walk()
            except Exception as exc:        # signed in, then the line dropped or a folder could not be listed
                steps.append({"step": "Files", "ok": False, "detail": f"the folders could not be listed ({type(exc).__name__}: {str(exc)[:120]})"})
                return {"ok": False, "steps": steps, "hostkey": ses.key_fp}
            remits = [f for f in files if re.search(r"(?i)\.(rmt|835|era)$", f[0])]
            if not files:
                steps.append({"step": "Files", "ok": False, "detail": "no remittance files in the folders read: in TriZetto's portal turn on "
                              "Auto Generate Remittances, or set TRIZETTO_SFTP_DIRS to the folder they are written to"})
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
        finally:
            ses.close()
    except SignInRefused as exc:
        steps.append({"step": "Sign-in", "ok": False, "detail": str(exc)})
        return {"ok": False, "steps": steps, "refused": str(exc)}
    except TriZettoError as exc:
        steps.append({"step": "Connection", "ok": False, "detail": str(exc)})
        return {"ok": False, "steps": steps}
