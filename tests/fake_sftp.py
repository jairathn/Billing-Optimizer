"""
A stand-in for TriZetto's SFTP server, for the tests: a real SSH server (paramiko) on a local port, one password, a
folder on disk as its file system, read only. `refuse_password` and a second host key test the sign-in and the key check.
"""
from __future__ import annotations

import os
import socket
import threading

import paramiko
from paramiko import SFTPAttributes, SFTPHandle, SFTPServer, SFTPServerInterface, ServerInterface
from paramiko.sftp import SFTP_NO_SUCH_FILE, SFTP_OK, SFTP_PERMISSION_DENIED

_KEYS: dict[int, paramiko.RSAKey] = {}


def host_key(n: int = 0) -> paramiko.RSAKey:
    if n not in _KEYS:
        _KEYS[n] = paramiko.RSAKey.generate(2048)
    return _KEYS[n]


class _Server(ServerInterface):
    def __init__(self, fake: "FakeSFTP"):
        self.fake = fake

    def check_auth_password(self, username, password):
        self.fake.logins.append(username)
        ok = username == self.fake.user and password == self.fake.password and not self.fake.refuse_password
        return paramiko.AUTH_SUCCESSFUL if ok else paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return "password"

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED


class _Handle(SFTPHandle):
    def stat(self):
        return SFTPAttributes.from_stat(os.fstat(self.readfile.fileno()))


class _SFTP(SFTPServerInterface):
    ROOT = ""

    def __init__(self, server, *a, **kw):
        super().__init__(server, *a, **kw)
        self.root = server.fake.root
        self.fake = server.fake

    def _real(self, path: str) -> str:
        path = self.canonicalize(path)
        return os.path.join(self.root, path.lstrip("/"))

    def canonicalize(self, path):
        path = os.path.normpath("/" + (path or "").replace("\\", "/")).replace("\\", "/")
        return "/" if path in ("/.", "/") else path

    def list_folder(self, path):
        real = self._real(path)
        try:
            out = []
            for name in os.listdir(real):
                attr = SFTPAttributes.from_stat(os.stat(os.path.join(real, name)))
                attr.filename = name
                out.append(attr)
            return out
        except OSError:
            return SFTP_NO_SUCH_FILE

    def stat(self, path):
        try:
            return SFTPAttributes.from_stat(os.stat(self._real(path)))
        except OSError:
            return SFTP_NO_SUCH_FILE

    lstat = stat

    def open(self, path, flags, attr):
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC):
            return SFTP_PERMISSION_DENIED          # read only, like the account RemitWatch is given
        try:
            fh = open(self._real(path), "rb")
        except OSError:
            return SFTP_NO_SUCH_FILE
        self.fake.reads.append(self.canonicalize(path))
        h = _Handle(flags)
        h.readfile = fh
        h.filename = self._real(path)
        return h

    def remove(self, path):
        self.fake.writes.append(("remove", path))
        return SFTP_PERMISSION_DENIED

    def rename(self, oldpath, newpath):
        self.fake.writes.append(("rename", oldpath))
        return SFTP_PERMISSION_DENIED

    def mkdir(self, path, attr):
        return SFTP_PERMISSION_DENIED

    def rmdir(self, path):
        return SFTP_PERMISSION_DENIED

    def chattr(self, path, attr):
        return SFTP_OK


class FakeSFTP:
    def __init__(self, root: str, user: str = "SITE123", password: str = "pw-123", key: int = 0):
        self.root, self.user, self.password = root, user, password
        self.key = host_key(key)
        self.refuse_password = False
        self.logins: list[str] = []
        self.reads: list[str] = []
        self.writes: list[tuple] = []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self._stop = False
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._one, args=(conn,), daemon=True).start()

    def _one(self, conn) -> None:
        t = paramiko.Transport(conn)
        t.add_server_key(self.key)
        server = _Server(self)
        t.set_subsystem_handler("sftp", SFTPServer, _SFTP)
        try:
            t.start_server(server=server)
        except Exception:
            return

    def env(self, **kw) -> dict:
        e = {"TRIZETTO_SFTP_USER": self.user, "TRIZETTO_SFTP_PASSWORD": self.password, "TRIZETTO_SFTP_HOST": "127.0.0.1",
             "TRIZETTO_SFTP_PORT": str(self.port)}
        e.update(kw)
        return e

    def stop(self) -> None:
        self._stop = True
        try:
            self.sock.close()
        except OSError:
            pass
