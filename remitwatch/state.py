"""
The state folder: where a serverless deployment keeps what must survive between runs.

Two implementations of one small interface. DropboxState keeps the files in a private folder of the practice's own
Dropbox, so no other storage service is needed. LocalState keeps them in a directory, for tests and for trying the
serverless mode on a laptop.

    get(name) -> bytes or None           put(name, data, mode="overwrite" | "add" | "update", rev=None) -> meta
    get_chunks(name) -> iterator or None  put_chunks(name, chunks) -> meta        (large files)
    meta(name) -> {"rev", "size", "modified"} or None
    list(prefix) -> [{"name", "rev", "size", "modified"}]        delete(name) -> bool

mode "add" refuses to replace an existing file and mode "update" replaces it only when its revision is still `rev`.
Both raise Conflict otherwise. That is all the locking the cycle needs.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Iterable, Iterator


class Conflict(Exception):
    pass


class LocalState:
    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)

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
        return {"rev": f"{st.st_mtime_ns}-{st.st_size}", "size": st.st_size,
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
        if mode == "add":
            try:
                with open(p, "xb") as fh:
                    fh.write(data)
            except FileExistsError as exc:
                raise Conflict(name) from exc
            return self.meta(name)
        if mode == "update":
            cur = self.meta(name)
            if not cur or cur["rev"] != rev:
                raise Conflict(name)
        tmp = p + ".part"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, p)
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
                if f.endswith(".part"):
                    continue
                name = os.path.relpath(os.path.join(dp, f), self.root).replace(os.sep, "/")
                out.append(dict(self.meta(name) or {}, name=name))
        return sorted(out, key=lambda m: m["name"])

    def delete(self, name: str) -> bool:
        try:
            os.remove(self._p(name))
            return True
        except OSError:
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

    def describe(self) -> str:
        return f"Dropbox folder {self.base}"


def state_from_env():
    """The state folder this deployment is configured for, or None when nothing is configured yet."""
    local = os.environ.get("REMITWATCH_STATE_DIR")
    if local:
        return LocalState(local)
    if all(os.environ.get(k) for k in ("DROPBOX_APP_KEY", "DROPBOX_APP_SECRET", "DROPBOX_REFRESH_TOKEN")):
        from .dropbox_sync import DropboxClient
        return DropboxState(DropboxClient(), os.environ.get("REMITWATCH_STATE_PATH", "/RemitWatch-state"))
    return None
