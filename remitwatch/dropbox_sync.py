"""
Unattended Dropbox sync.

Uses the Dropbox HTTP API directly (no SDK dependency) with an app refresh
token, so a scheduled job can run forever without a human logging in.

Environment:
    DROPBOX_APP_KEY, DROPBOX_APP_SECRET, DROPBOX_REFRESH_TOKEN   required
    DROPBOX_ROOT_PATH          folder to watch, default "/Trizetto Data"
    DROPBOX_PATH_ROOT_NS       optional namespace id; set when the folder lives in a team space
    REMITWATCH_DATA_DIR        local mirror, default data/dropbox
    REMITWATCH_DB              SQLite store, default data/remitwatch.sqlite

Flow: list the folder (full listing the first time, cursor delta afterwards)
-> download new or changed files that look like remits, EMA exports or 837s
-> ingest -> detect -> report. The listing cursor is stored in the store's
meta table so each weekly run only touches what changed.

One-time setup: `python -m remitwatch.cli oauth` prints the authorization
link, takes the code, and prints the refresh token to put in the environment.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Iterable, Any, Iterator

import requests

API = "https://api.dropboxapi.com/2"
CONTENT = "https://content.dropboxapi.com/2"
TOKEN_URL = "https://api.dropboxapi.com/oauth2/token"
AUTH_URL = "https://www.dropbox.com/oauth2/authorize"

WANTED_EXT = (".rmt", ".835", ".era", ".edi", ".xlsx", ".txt", ".x12", ".dat")


def dropbox_content_hash(path: str) -> str:
    """Dropbox content hash: SHA-256 of the concatenated SHA-256s of 4 MiB blocks."""
    block = 4 * 1024 * 1024
    digests = []
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(block)
            if not chunk:
                break
            digests.append(hashlib.sha256(chunk).digest())
    return hashlib.sha256(b"".join(digests)).hexdigest()


def content_hash_bytes(data: bytes) -> str:
    """The same hash for data already in memory."""
    block = 4 * 1024 * 1024
    digests = [hashlib.sha256(data[i:i + block]).digest() for i in range(0, len(data), block)]
    return hashlib.sha256(b"".join(digests)).hexdigest()


class DropboxConflict(Exception):
    """A write was refused because the file exists or has changed since it was read."""


class DropboxNotFound(Exception):
    pass


class DropboxAuthError(RuntimeError):
    """Dropbox refused the credentials, or the token lacks a permission. Retrying does not help: someone has to fix the
    app's permissions or replace the token."""


def _auth_error(endpoint: str, status: int, text: str) -> DropboxAuthError:
    if "missing_scope" in text:
        try:
            scope = json.loads(text)["error"].get("required_scope") or ""
        except Exception:
            scope = ""
        return DropboxAuthError(f"The Dropbox token is missing the permission {scope or 'this call needs'}. Tick it on the app's Permissions tab, "
                                "save, then connect Dropbox again: a token only carries the permissions ticked when it was made.")
    return DropboxAuthError(f"Dropbox refused the token ({endpoint}, {status}): {text[:200]}")


class DropboxClient:
    def __init__(self, app_key: str | None = None, app_secret: str | None = None, refresh_token: str | None = None,
                 path_root_ns: str | None = None, session: requests.Session | None = None):
        self.app_key = app_key or os.environ.get("DROPBOX_APP_KEY", "")
        self.app_secret = app_secret or os.environ.get("DROPBOX_APP_SECRET", "")
        self.refresh_token = refresh_token or os.environ.get("DROPBOX_REFRESH_TOKEN", "")
        self.path_root_ns = path_root_ns or os.environ.get("DROPBOX_PATH_ROOT_NS") or None
        self.s = session or requests.Session()
        if session is None:
            adapter = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=16)
            self.s.mount("https://", adapter)
        self._token: str | None = None
        self._token_exp = 0.0

    # ---------------------------------------------------------------- auth
    @staticmethod
    def authorize_url(app_key: str) -> str:
        return f"{AUTH_URL}?client_id={app_key}&response_type=code&token_access_type=offline"

    @staticmethod
    def exchange_code(app_key: str, app_secret: str, code: str) -> dict:
        r = requests.post(TOKEN_URL, data={"code": code, "grant_type": "authorization_code"}, auth=(app_key, app_secret), timeout=30)
        r.raise_for_status()
        return r.json()

    def access_token(self) -> str:
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        if not (self.app_key and self.app_secret and self.refresh_token):
            raise RuntimeError("DROPBOX_APP_KEY, DROPBOX_APP_SECRET and DROPBOX_REFRESH_TOKEN must be set")
        r = self.s.post(TOKEN_URL, data={"grant_type": "refresh_token", "refresh_token": self.refresh_token}, auth=(self.app_key, self.app_secret), timeout=30)
        if r.status_code in (400, 401):
            raise DropboxAuthError(f"Dropbox refused the app key, app secret or refresh token: {r.text[:200]}")
        r.raise_for_status()
        j = r.json()
        self._token = j["access_token"]
        self._token_exp = time.time() + int(j.get("expires_in", 14400))
        return self._token

    def _headers(self, extra: dict | None = None) -> dict:
        h = {"Authorization": f"Bearer {self.access_token()}"}
        if self.path_root_ns:
            h["Dropbox-API-Path-Root"] = json.dumps({".tag": "namespace_id", "namespace_id": str(self.path_root_ns)})
        if extra:
            h.update(extra)
        return h

    def _rpc(self, endpoint: str, body: dict) -> dict:
        refreshed = False
        for attempt in range(6):
            r = self.s.post(f"{API}/{endpoint}", headers=self._headers({"Content-Type": "application/json"}), json=body, timeout=60)
            if r.status_code == 429 or r.status_code >= 500:
                wait = float(r.headers.get("Retry-After", 2 ** attempt))
                time.sleep(min(wait, 60))
                continue
            if r.status_code == 401:
                if "expired_access_token" in r.text and not refreshed:
                    self._token, refreshed = None, True
                    continue
                raise _auth_error(endpoint, r.status_code, r.text)
            if r.status_code != 200:
                raise RuntimeError(f"Dropbox {endpoint} failed {r.status_code}: {r.text[:300]}")
            return r.json()
        raise RuntimeError(f"Dropbox {endpoint}: too many retries")

    # ---------------------------------------------------------------- listing
    def list_folder(self, path: str, recursive: bool = True) -> Iterator[dict]:
        body = {"path": path, "recursive": recursive, "limit": 2000, "include_deleted": False, "include_non_downloadable_files": False}
        res = self._rpc("files/list_folder", body)
        while True:
            for e in res.get("entries", []):
                yield e
            if not res.get("has_more"):
                self.last_cursor = res.get("cursor")
                return
            res = self._rpc("files/list_folder/continue", {"cursor": res["cursor"]})

    def list_continue(self, cursor: str) -> Iterator[dict]:
        res = self._rpc("files/list_folder/continue", {"cursor": cursor})
        while True:
            for e in res.get("entries", []):
                yield e
            if not res.get("has_more"):
                self.last_cursor = res.get("cursor")
                return
            res = self._rpc("files/list_folder/continue", {"cursor": res["cursor"]})

    def latest_cursor(self, path: str, recursive: bool = True) -> str:
        return self._rpc("files/list_folder/get_latest_cursor", {"path": path, "recursive": recursive})["cursor"]

    # ---------------------------------------------------------------- download
    def download(self, path_or_id: str, dest: str) -> dict:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        for attempt in range(6):
            r = self.s.post(f"{CONTENT}/files/download", headers=self._headers({"Dropbox-API-Arg": json.dumps({"path": path_or_id})}), stream=True, timeout=120)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(min(float(r.headers.get("Retry-After", 2 ** attempt)), 60))
                continue
            if r.status_code != 200:
                raise RuntimeError(f"download {path_or_id} failed {r.status_code}: {r.text[:200]}")
            tmp = dest + ".part"
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(1 << 20):
                    fh.write(chunk)
            os.replace(tmp, dest)
            return json.loads(r.headers.get("Dropbox-API-Result", "{}"))
        raise RuntimeError(f"download {path_or_id}: too many retries")


    def download_bytes(self, path_or_id: str) -> tuple[bytes, dict]:
        refreshed = False
        for attempt in range(6):
            r = self.s.post(f"{CONTENT}/files/download", headers=self._headers({"Dropbox-API-Arg": json.dumps({"path": path_or_id})}), timeout=300)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(min(float(r.headers.get("Retry-After", 2 ** attempt)), 60))
                continue
            if r.status_code == 401:
                if "expired_access_token" in r.text and not refreshed:
                    self._token, refreshed = None, True
                    continue
                raise _auth_error("files", r.status_code, r.text)
            if r.status_code == 409 and "not_found" in r.text:
                raise DropboxNotFound(path_or_id)
            if r.status_code != 200:
                raise RuntimeError(f"download {path_or_id} failed {r.status_code}: {r.text[:200]}")
            return r.content, json.loads(r.headers.get("Dropbox-API-Result", "{}"))
        raise RuntimeError(f"download {path_or_id}: too many retries")

    def download_chunks(self, path_or_id: str, chunk: int = 4 << 20) -> Iterator[bytes]:
        """Stream a large file without holding it in one piece."""
        refreshed = False
        for attempt in range(6):
            r = self.s.post(f"{CONTENT}/files/download", headers=self._headers({"Dropbox-API-Arg": json.dumps({"path": path_or_id})}), stream=True, timeout=300)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(min(float(r.headers.get("Retry-After", 2 ** attempt)), 60))
                continue
            if r.status_code == 401:
                if "expired_access_token" in r.text and not refreshed:
                    self._token, refreshed = None, True
                    continue
                raise _auth_error("files", r.status_code, r.text)
            if r.status_code == 409 and "not_found" in r.text:
                raise DropboxNotFound(path_or_id)
            if r.status_code != 200:
                raise RuntimeError(f"download {path_or_id} failed {r.status_code}: {r.text[:200]}")
            yield from r.iter_content(chunk)
            return
        raise RuntimeError(f"download {path_or_id}: too many retries")

    # ---------------------------------------------------------------- writing (needs the files.content.write permission)
    @staticmethod
    def _mode(mode: str, rev: str | None) -> dict | str:
        if mode == "update":
            return {".tag": "update", "update": rev}
        return mode     # "add" or "overwrite"

    def _content_call(self, endpoint: str, arg: dict, data: bytes) -> dict:
        refreshed = False
        for attempt in range(6):
            r = self.s.post(f"{CONTENT}/{endpoint}", headers=self._headers({"Dropbox-API-Arg": json.dumps(arg), "Content-Type": "application/octet-stream"}),
                            data=data, timeout=600)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(min(float(r.headers.get("Retry-After", 2 ** attempt)), 60))
                continue
            if r.status_code == 401:
                if "expired_access_token" in r.text and not refreshed:
                    self._token, refreshed = None, True
                    continue
                raise _auth_error("files", r.status_code, r.text)
            if r.status_code == 409 and "conflict" in r.text:
                raise DropboxConflict(r.text[:200])
            if r.status_code != 200:
                raise RuntimeError(f"Dropbox {endpoint} failed {r.status_code}: {r.text[:300]}")
            return r.json() if r.text and r.text != "null" else {}
        raise RuntimeError(f"Dropbox {endpoint}: too many retries")

    def upload(self, path: str, data: bytes, mode: str = "overwrite", rev: str | None = None) -> dict:
        """Write a file of up to 150 MB in one call. mode "add" refuses to replace an existing file (DropboxConflict);
        mode "update" replaces it only if its revision is still `rev`."""
        if len(data) > 140 * 1024 * 1024:
            return self.upload_chunks(path, [data[i:i + (32 << 20)] for i in range(0, len(data), 32 << 20)])
        arg = {"path": path, "mode": self._mode(mode, rev), "autorename": False, "mute": True, "strict_conflict": mode != "overwrite"}
        return self._content_call("files/upload", arg, data)

    def upload_chunks(self, path: str, chunks: Iterable[bytes], part: int = 32 << 20) -> dict:
        """Write a large file through an upload session, overwriting. Chunks of any size are regrouped into parts that are
        multiples of 4 MiB, which the session requires for every part but the last."""
        sid = self._content_call("files/upload_session/start", {"close": False}, b"")["session_id"]
        offset, buf = 0, bytearray()
        for c in chunks:
            buf += c
            while len(buf) >= part:
                piece, buf = bytes(buf[:part]), buf[part:]
                self._content_call("files/upload_session/append_v2", {"cursor": {"session_id": sid, "offset": offset}, "close": False}, piece)
                offset += len(piece)
        return self._content_call("files/upload_session/finish",
                                  {"cursor": {"session_id": sid, "offset": offset},
                                   "commit": {"path": path, "mode": "overwrite", "autorename": False, "mute": True}}, bytes(buf))

    def get_metadata(self, path: str) -> dict | None:
        try:
            return self._rpc("files/get_metadata", {"path": path})
        except RuntimeError as exc:
            if "not_found" in str(exc):
                return None
            raise

    def delete(self, path: str) -> bool:
        try:
            self._rpc("files/delete_v2", {"path": path})
            return True
        except RuntimeError as exc:
            if "not_found" in str(exc):
                return False
            raise

    def create_folder(self, path: str) -> None:
        try:
            self._rpc("files/create_folder_v2", {"path": path, "autorename": False})
        except RuntimeError as exc:
            if "conflict" not in str(exc):
                raise

    def current_account(self) -> dict:
        r = self.s.post(f"{API}/users/get_current_account", headers=self._headers(), timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"Dropbox users/get_current_account failed {r.status_code}: {r.text[:200]}")
        return r.json()


def wanted(entry: dict) -> bool:
    if entry.get(".tag") != "file":
        return False
    name = entry.get("name", "").lower()
    if name.startswith("~$") or name.startswith("."):
        return False
    if name.endswith(".zip"):
        return False
    return name.endswith(WANTED_EXT)


def local_path(data_dir: str, entry: dict) -> str:
    rel = (entry.get("path_display") or entry.get("path_lower") or entry["name"]).lstrip("/")
    return os.path.join(data_dir, rel)


def sync(store, client: DropboxClient | None = None, root_path: str | None = None, data_dir: str | None = None,
         full: bool = False, log=print) -> dict:
    """Mirror new/changed wanted files from Dropbox into data_dir. Returns a summary with the list of new local files."""
    client = client or DropboxClient()
    root_path = root_path or os.environ.get("DROPBOX_ROOT_PATH", "/Trizetto Data")
    data_dir = data_dir or os.environ.get("REMITWATCH_DATA_DIR", "data/dropbox")
    conn = store.conn
    cursor = None if full else (conn.execute("SELECT value FROM meta WHERE key='sync:cursor'").fetchone() or [None])[0]
    stats = {"listed": 0, "wanted": 0, "downloaded": 0, "unchanged": 0, "errors": 0, "new_files": [], "mode": "delta" if cursor else "full"}
    t0 = time.time()
    try:
        entries = client.list_continue(cursor) if cursor else client.list_folder(root_path, recursive=True)
        for e in entries:
            stats["listed"] += 1
            if not wanted(e):
                continue
            stats["wanted"] += 1
            dest = local_path(data_dir, e)
            if os.path.exists(dest) and os.path.getsize(dest) == e.get("size") and (not e.get("content_hash") or dropbox_content_hash(dest) == e["content_hash"]):
                stats["unchanged"] += 1
                continue
            try:
                client.download(e.get("id") or e["path_lower"], dest)
                stats["downloaded"] += 1
                stats["new_files"].append(dest)
                if stats["downloaded"] % 100 == 0:
                    log(f"  downloaded {stats['downloaded']} files")
            except Exception as exc:
                stats["errors"] += 1
                log(f"  ERROR {e.get('path_display')}: {exc}")
        new_cursor = getattr(client, "last_cursor", None)
    except Exception as exc:
        if cursor and "reset" in str(exc).lower():
            log("cursor reset by Dropbox; doing a full listing")
            conn.execute("DELETE FROM meta WHERE key='sync:cursor'")
            conn.commit()
            return sync(store, client, root_path, data_dir, full=True, log=log)
        raise
    if new_cursor:
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('sync:cursor', ?)", (new_cursor,))
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('sync:last_run', ?)", (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),))
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('sync:last_stats', ?)", (json.dumps({k: v for k, v in stats.items() if k != 'new_files'}),))
    conn.commit()
    stats["seconds"] = round(time.time() - t0, 1)
    return stats


def ingest_new_files(store, paths: list[str], log=print) -> dict:
    """Route downloaded files to the right loader."""
    from .ema_exports import iter_rows
    from .x12_835 import looks_like_x12

    out = {"remits": 0, "ema": 0, "claims_837": 0, "skipped": 0, "errors": 0}
    remits = []
    for p in paths:
        low = p.lower()
        if low.endswith(".xlsx"):
            try:
                with open(p, "rb") as fh:
                    sha = hashlib.sha256(fh.read()).hexdigest()
                if store.file_seen(sha):
                    out["skipped"] += 1
                    continue
                rows = list(iter_rows(p))
                r = store.ingest_ema_rows(rows)
                fam = rows[0]["family"] if rows else "unknown"
                store.record_file(sha, p, kind=f"ema:{fam}", status="ok", lines=len(rows), duplicates=len(rows) - r["inserted"])
                out["ema"] += 1
            except Exception as exc:
                out["errors"] += 1
                log(f"  EMA ERROR {os.path.basename(p)}: {exc}")
        elif looks_like_x12(p):
            with open(p, "rb") as fh:
                head = fh.read(400)
            if b"*837*" in head or b"GS*HC*" in head:
                try:
                    from .x12_837 import ingest_837_file
                    ingest_837_file(store, p)
                    out["claims_837"] += 1
                except Exception as exc:
                    out["errors"] += 1
                    log(f"  837 ERROR {os.path.basename(p)}: {exc}")
            else:
                remits.append(p)
        else:
            out["skipped"] += 1
    if remits:
        st = store.ingest_835_paths(remits)
        out["remits"] = st["ingested"]
        out["remit_stats"] = st
        store.mark_superseded()
    return out
