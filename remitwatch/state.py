"""
The state folder: where a serverless deployment keeps what must survive between runs.

Two implementations of one small interface. DropboxState keeps the files in a private folder of the practice's own
Dropbox, so no other storage service is needed. LocalState keeps them in a directory, for tests and for trying the
serverless mode on a laptop.

    get(name) -> bytes or None           put(name, data, mode="overwrite" | "add" | "update", rev=None) -> meta
    get_chunks(name) -> iterator or None  put_chunks(name, chunks) -> meta        (large files)
    meta(name) -> {"rev", "size", "modified"} or None
    list(prefix) -> [{"name", "rev", "size", "modified"}]        delete(name) -> bool
    move(name, to) -> bool                                        (the intake folder's pairs go to intake/done/)

mode "add" refuses to replace an existing file and mode "update" replaces it only when its revision is still `rev`.
Both raise Conflict otherwise. That is all the locking the cycle needs.
"""
from __future__ import annotations

import os
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterable, Iterator

try:
    import fcntl
except ImportError:              # Windows: the writes of one process still take turns (threading lock below)
    fcntl = None

_UMASK = os.umask(0o022)         # read once (setting it back at once): a temporary file gets the mode open() would give
os.umask(_UMASK)


class Conflict(Exception):
    pass


class LocalState:
    LOCK_FILE = ".state.lock"       # held while a write checks and replaces a file, by every process using the folder
    _thread_lock = threading.Lock()

    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)

    @contextmanager
    def _locked(self):
        """One writer at a time in this folder, across threads and processes: a conditional write's check, its write
        and its rename happen as one step."""
        with LocalState._thread_lock:
            if fcntl is None:
                yield
                return
            with open(os.path.join(self.root, self.LOCK_FILE), "a+b") as fh:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def _replace(self, p: str, chunks: Iterable[bytes]) -> None:
        """Write to a temporary file of its own beside the target, then rename it over the target: a reader sees the old
        file or the new one, and two writers never share a temporary file."""
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p), prefix="." + os.path.basename(p) + ".", suffix=".part")
        try:
            with os.fdopen(fd, "wb") as fh:
                for c in chunks:
                    fh.write(c)
            os.chmod(tmp, 0o666 & ~_UMASK)
            os.replace(tmp, p)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    def _p(self, name: str) -> str:
        p = os.path.abspath(os.path.join(self.root, name))
        if not p.startswith(self.root + os.sep):
            raise ValueError("name escapes the state folder")
        return p

    def meta(self, name: str) -> dict | None:
        try:
            st = os.stat(self._p(name))
        except OSError:
            return None
        # the inode changes with every write (each is a new file renamed into place), so the revision does even where the
        # file system's clock is coarse and two writes have the same size
        return {"rev": f"{st.st_mtime_ns}-{st.st_size}-{st.st_ino}", "size": st.st_size,
                "modified": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(timespec="seconds")}

    def get(self, name: str) -> bytes | None:
        try:
            with open(self._p(name), "rb") as fh:
                return fh.read()
        except OSError:
            return None

    def get_chunks(self, name: str, chunk: int = 4 << 20) -> Iterator[bytes] | None:
        p = self._p(name)
        if not os.path.exists(p):
            return None

        def gen():
            with open(p, "rb") as fh:
                while True:
                    b = fh.read(chunk)
                    if not b:
                        return
                    yield b
        return gen()

    def put(self, name: str, data: bytes, mode: str = "overwrite", rev: str | None = None) -> dict:
        p = self._p(name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with self._locked():          # the check and the write as one step: two saves from one revision, one wins
            if mode == "add" and os.path.exists(p):
                raise Conflict(name)
            if mode == "update":
                cur = self.meta(name)
                if not cur or cur["rev"] != rev:
                    raise Conflict(name)
            self._replace(p, [data])
            return self.meta(name)

    def put_chunks(self, name: str, chunks: Iterable[bytes]) -> dict:
        p = self._p(name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".part"
        with open(tmp, "wb") as fh:
            for c in chunks:
                fh.write(c)
        os.replace(tmp, p)
        return self.meta(name)

    def list(self, prefix: str = "") -> list[dict]:
        base = self._p(prefix.rstrip("/")) if prefix.strip("/") else self.root
        out = []
        for dp, _dirs, files in os.walk(base):
            for f in files:
                if f.endswith(".part") or (dp == self.root and f == self.LOCK_FILE):
                    continue
                name = os.path.relpath(os.path.join(dp, f), self.root).replace(os.sep, "/")
                out.append(dict(self.meta(name) or {}, name=name))
        return sorted(out, key=lambda m: m["name"])

    def delete(self, name: str) -> bool:
        p = self._p(name)
        with self._locked():          # not between a conditional write's check and its rename
            try:
                os.remove(p)
                return True
            except OSError:
                return False

    def delete_many(self, names: list[str]) -> int:
        return sum(1 for n in names if self.delete(n))

    def move(self, name: str, to: str) -> bool:
        """Move a file inside the folder (a file already at `to` is replaced). False when there is no such file."""
        src, dst = self._p(name), self._p(to)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with self._locked():
            try:
                os.replace(src, dst)
                return True
            except FileNotFoundError:
                return False

    def describe(self) -> str:
        return f"local folder {self.root}"


class DropboxState:
    def __init__(self, client, base: str = "/RemitWatch-state"):
        self.client = client
        self.base = "/" + base.strip("/")

    def _p(self, name: str) -> str:
        return f"{self.base}/{name.strip('/')}"

    @staticmethod
    def _meta(e: dict | None) -> dict | None:
        if not e or e.get(".tag", "file") != "file":
            return None
        return {"rev": e.get("rev"), "size": e.get("size"), "modified": e.get("server_modified"), "content_hash": e.get("content_hash")}

    def meta(self, name: str) -> dict | None:
        return self._meta(self.client.get_metadata(self._p(name)))

    def get(self, name: str) -> bytes | None:
        from .dropbox_sync import DropboxNotFound
        try:
            return self.client.download_bytes(self._p(name))[0]
        except DropboxNotFound:
            return None

    def get_chunks(self, name: str, chunk: int = 4 << 20) -> Iterator[bytes] | None:
        if self.meta(name) is None:
            return None
        return self.client.download_chunks(self._p(name), chunk)

    def put(self, name: str, data: bytes, mode: str = "overwrite", rev: str | None = None) -> dict:
        from .dropbox_sync import DropboxConflict
        try:
            return self._meta(self.client.upload(self._p(name), data, mode=mode, rev=rev)) or {}
        except DropboxConflict as exc:
            raise Conflict(name) from exc

    def put_chunks(self, name: str, chunks: Iterable[bytes]) -> dict:
        return self._meta(self.client.upload_chunks(self._p(name), chunks)) or {}

    def list(self, prefix: str = "") -> list[dict]:
        path = self._p(prefix) if prefix.strip("/") else self.base
        try:
            entries = list(self.client.list_folder(path, recursive=True))
        except RuntimeError as exc:
            if "not_found" in str(exc):
                return []
            raise
        out = []
        for e in entries:
            m = self._meta(e)
            if m:
                out.append(dict(m, name=e["path_display"][len(self.base) + 1:] if e.get("path_display", "").lower().startswith(self.base.lower() + "/")
                                else e["name"]))
        return sorted(out, key=lambda m: m["name"])

    def delete(self, name: str) -> bool:
        return self.client.delete(self._p(name))

    def delete_many(self, names: list[str]) -> int:
        """Many small files in a few calls: a day of status changes is hundreds of journal entries."""
        return self.client.delete_many([self._p(n) for n in names]) if names else 0

    def move(self, name: str, to: str) -> bool:
        """Move a file inside the folder (Dropbox moves it; a file already at `to` is replaced). False when there is no
        such file. A client with no move of its own copies the file and deletes the original."""
        from .dropbox_sync import DropboxConflict, DropboxNotFound
        mover = getattr(self.client, "move", None)
        if mover is None:
            data = self.get(name)
            if data is None:
                return False
            self.put(to, data)
            self.delete(name)
            return True
        for _ in range(2):
            try:
                mover(self._p(name), self._p(to))
                return True
            except DropboxNotFound:
                return False
            except DropboxConflict:
                self.delete(to)         # an earlier move of the same name whose answer was lost: the newer one wins
        mover(self._p(name), self._p(to))
        return True

    def describe(self) -> str:
        return f"Dropbox folder {self.base}"


def state_from_env(fast: bool = False):
    """The state folder this deployment is configured for, or None when nothing is configured yet. `fast` is for the
    dashboard's own small reads: one attempt and a short wait, so a slow Dropbox cannot hold every page of the site."""
    local = os.environ.get("REMITWATCH_STATE_DIR")
    if local:
        return LocalState(local)
    if all(os.environ.get(k) for k in ("DROPBOX_APP_KEY", "DROPBOX_APP_SECRET", "DROPBOX_REFRESH_TOKEN")):
        from .dropbox_sync import DropboxClient
        client = DropboxClient(attempts=1, timeout=8) if fast else DropboxClient()
        return DropboxState(client, os.environ.get("REMITWATCH_STATE_PATH", "/RemitWatch-state"))
    return None
