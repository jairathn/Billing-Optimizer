"""
A stand-in for the Dropbox API backed by a local directory, with the same methods and error behaviour as
remitwatch.dropbox_sync.DropboxClient. Used by the tests and by scripts/cloud_bench.py to run the serverless flow
end to end without credentials.
"""
from __future__ import annotations

import hashlib
import os
import time
from typing import Iterable, Iterator

from remitwatch.dropbox_sync import DropboxConflict, DropboxNotFound, content_hash_bytes


class FakeDropbox:
    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)
        self.last_cursor: str | None = None
        self._hash_cache: dict[tuple, str] = {}
        self._snapshots: dict[str, dict[str, str]] = {}
        self.calls: dict[str, int] = {}
        self.app_secret = "fake-secret"
        self.broken: dict[str, Exception] = {}

    # ------------------------------------------------------------ helpers
    def _count(self, name: str) -> None:
        self.calls[name] = self.calls.get(name, 0) + 1

    def _local(self, path: str) -> str:
        if path.startswith("id:"):
            path = path[3:]
        p = os.path.abspath(os.path.join(self.root, path.lstrip("/")))
        if not (p + os.sep).startswith(self.root + os.sep):
            raise ValueError("path escapes the fake Dropbox")
        return p

    def _hash(self, p: str) -> str:
        st = os.stat(p)
        key = (p, st.st_mtime_ns, st.st_size)
        if key not in self._hash_cache:
            with open(p, "rb") as fh:
                self._hash_cache[key] = content_hash_bytes(fh.read())
        return self._hash_cache[key]

    def _entry(self, p: str) -> dict:
        rel = "/" + os.path.relpath(p, self.root).replace(os.sep, "/")
        st = os.stat(p)
        return {".tag": "file", "name": os.path.basename(p), "path_lower": rel.lower(), "path_display": rel, "id": "id:" + rel,
                "size": st.st_size, "content_hash": self._hash(p), "rev": f"{st.st_mtime_ns:x}{st.st_size:x}",
                "server_modified": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(st.st_mtime)), "is_downloadable": True}

    def _walk(self, path: str, recursive: bool) -> list[dict]:
        base = self._local(path) if path else self.root
        if not os.path.isdir(base):
            raise RuntimeError(f"Dropbox files/list_folder failed 409: path/not_found/ {path}")
        out = []
        for dp, dirs, files in os.walk(base):
            for f in sorted(files):
                if not f.endswith(".part"):
                    out.append(self._entry(os.path.join(dp, f)))
            if not recursive:
                break
        return out

    # ------------------------------------------------------------ listing
    def list_folder(self, path: str, recursive: bool = True) -> Iterator[dict]:
        self._count("list_folder")
        entries = self._walk(path, recursive)
        cursor = hashlib.sha1(f"{path}|{time.time_ns()}".encode()).hexdigest()
        self._snapshots[cursor] = {"__path__": path, **{e["id"]: e["content_hash"] for e in entries}}
        self.last_cursor = cursor
        yield from entries

    def _delta(self, cursor: str) -> tuple[list[dict], str]:
        snap = self._snapshots.get(cursor)
        if snap is None:
            raise RuntimeError("Dropbox files/list_folder/continue failed 409: reset/")
        path = snap["__path__"]
        entries = self._walk(path, True)
        changed = [e for e in entries if snap.get(e["id"]) != e["content_hash"]]
        new = hashlib.sha1(f"{cursor}|{time.time_ns()}".encode()).hexdigest()
        self._snapshots[new] = {"__path__": path, **{e["id"]: e["content_hash"] for e in entries}}
        return changed, new

    def list_continue(self, cursor: str) -> Iterator[dict]:
        self._count("list_continue")
        changed, new = self._delta(cursor)
        self.last_cursor = new
        yield from changed

    def _rpc(self, endpoint: str, body: dict) -> dict:
        self._count(endpoint)
        if endpoint == "files/list_folder/continue":
            changed, new = self._delta(body["cursor"])
            return {"entries": changed, "cursor": new, "has_more": False}
        if endpoint == "files/list_folder":
            return {"entries": self._walk(body.get("path", ""), bool(body.get("recursive")))[: body.get("limit", 2000)], "cursor": "c", "has_more": False}
        raise RuntimeError(f"fake Dropbox: unsupported endpoint {endpoint}")

    # ------------------------------------------------------------ files
    def get_metadata(self, path: str) -> dict | None:
        self._count("get_metadata")
        p = self._local(path)
        return self._entry(p) if os.path.isfile(p) else None

    def download_bytes(self, path_or_id: str) -> tuple[bytes, dict]:
        self._count("download")
        p = self._local(path_or_id)
        for part, exc in self.broken.items():           # tests: make chosen files fail to download
            if part in p:
                raise exc
        if not os.path.isfile(p):
            raise DropboxNotFound(path_or_id)
        with open(p, "rb") as fh:
            return fh.read(), self._entry(p)

    def download_chunks(self, path_or_id: str, chunk: int = 4 << 20) -> Iterator[bytes]:
        data, _ = self.download_bytes(path_or_id)
        for i in range(0, len(data), chunk):
            yield data[i:i + chunk]

    def upload(self, path: str, data: bytes, mode: str = "overwrite", rev: str | None = None) -> dict:
        self._count("upload")
        p = self._local(path)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        exists = os.path.isfile(p)
        if mode == "add" and exists:
            raise DropboxConflict("path/conflict/file/")
        if mode == "update" and (not exists or self._entry(p)["rev"] != rev):
            raise DropboxConflict("path/conflict/file/")
        tmp = p + ".part"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, p)
        return self._entry(p)

    def upload_chunks(self, path: str, chunks: Iterable[bytes], part: int = 32 << 20) -> dict:
        return self.upload(path, b"".join(chunks))

    def delete(self, path: str) -> bool:
        self._count("delete")
        p = self._local(path)
        if os.path.isfile(p):
            os.remove(p)
            return True
        return False

    def delete_many(self, paths: list[str], wait_s: int = 60) -> int:
        self._count("delete_batch")
        for p in paths:
            lp = self._local(p)
            if os.path.isfile(lp):
                os.remove(lp)
        return len(paths)

    def current_account(self) -> dict:
        return {"name": {"display_name": "Test Account"}, "root_info": {"root_namespace_id": "1", "home_namespace_id": "1"}}
