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
from typing import Iterable, Iterator

import requests

API = "https://api.dropboxapi.com/2"
CONTENT = "https://content.dropboxapi.com/2"
TOKEN_URL = "https://api.dropboxapi.com/oauth2/token"
AUTH_URL = "https://www.dropbox.com/oauth2/authorize"

WANTED_EXT = (".rmt", ".835", ".era", ".edi", ".xlsx", ".txt", ".x12", ".dat", ".csv", ".tsv", ".zip", ".gz")
REPORT_TIMEOUT = (10, 30)           # (connect, read) seconds for every Dropbox read of an EMA report: a stalled stream fails
                                    # in 30 s instead of outliving a serverless run
MAX_EXPORT_BYTES = 60 * 1024 * 1024     # one of the three weekly exports is read whole in memory: a larger one is refused
MAX_X12_BYTES = 64 * 1024 * 1024        # a remittance or claim file is read whole: the real ones are under 200 KB
REPORT_EXTS = (".xlsx", ".csv", ".tsv", ".txt", ".zip", ".gz")     # endings the router asks the report reader about first
PENDING_TRIES = 5                   # cycles a fetched file is offered to the readers before it is listed as not read
ZIP_CHUNK_BYTES = 64 * 1024 * 1024  # the members of a zip are unpacked and read this much at a time (a serverless host's
                                    # temporary folder is 512 MB)


def backfill_zip_name() -> str:
    """The archive of remittances read once on a serverless host's first load (cloud.ingest_zip), never as a report."""
    return os.environ.get("REMITWATCH_BACKFILL_ZIP", "Trizetto.zip")


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
                 path_root_ns: str | None = None, session: requests.Session | None = None, attempts: int = 6, timeout: int = 60):
        # attempts=1 with a short timeout is for the dashboard's own small checks: a slow Dropbox must fail fast there,
        # because every page of the site waits on them. The cycle keeps the patient default.
        self.attempts = max(1, attempts)
        self.timeout = timeout
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
        for attempt in range(self.attempts):
            r = self.s.post(f"{API}/{endpoint}", headers=self._headers({"Content-Type": "application/json"}), json=body, timeout=self.timeout)
            if r.status_code == 429 or r.status_code >= 500 or (r.status_code == 409 and "too_many_write_operations" in r.text):
                if attempt + 1 < self.attempts:
                    time.sleep(min(float(r.headers.get("Retry-After") or 0) or 2 ** attempt, 60))
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
        for attempt in range(self.attempts):
            r = self.s.post(f"{CONTENT}/files/download", headers=self._headers({"Dropbox-API-Arg": json.dumps({"path": path_or_id})}),
                            timeout=max(self.timeout, 60) * 5 if self.attempts > 1 else 60)
            if r.status_code == 429 or r.status_code >= 500:
                if attempt + 1 < self.attempts:
                    time.sleep(min(float(r.headers.get("Retry-After") or 0) or 2 ** attempt, 60))
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

    def download_chunks(self, path_or_id: str, chunk: int = 4 << 20, timeout=REPORT_TIMEOUT) -> Iterator[bytes]:
        """Stream a large file without holding it in one piece. `timeout` is (connect, read) in seconds: the read part is
        the longest wait for the next bytes, so a stalled stream fails instead of hanging."""
        refreshed = False
        for attempt in range(self.attempts):
            r = self.s.post(f"{CONTENT}/files/download", headers=self._headers({"Dropbox-API-Arg": json.dumps({"path": path_or_id})}),
                            stream=True, timeout=timeout)
            if r.status_code == 429 or r.status_code >= 500:
                if attempt + 1 < self.attempts:
                    time.sleep(min(float(r.headers.get("Retry-After") or 0) or 2 ** attempt, 60))
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
            try:
                yield from r.iter_content(chunk)
            finally:
                r.close()       # a reader that stops early (the first 64 KB of a large report) lets the rest go
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
        for attempt in range(self.attempts):
            r = self.s.post(f"{CONTENT}/{endpoint}", headers=self._headers({"Dropbox-API-Arg": json.dumps(arg), "Content-Type": "application/octet-stream"}),
                            data=data, timeout=600 if self.attempts > 1 else 60)
            # "too many write operations" is Dropbox asking for the same write again in a moment; it arrives as a 409
            if r.status_code == 429 or r.status_code >= 500 or (r.status_code == 409 and "too_many_write_operations" in r.text):
                if attempt + 1 < self.attempts:
                    time.sleep(min(float(r.headers.get("Retry-After") or 0) or 2 ** attempt, 60))
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

    def move(self, from_path: str, to_path: str) -> dict:
        """Move a file inside Dropbox (files/move_v2; needs the files.content.write permission). A file already at the
        destination raises DropboxConflict; a source that is gone raises DropboxNotFound."""
        try:
            res = self._rpc("files/move_v2", {"from_path": from_path, "to_path": to_path, "autorename": False,
                                              "allow_ownership_transfer": False})
        except RuntimeError as exc:
            text = str(exc)
            if "from_lookup/not_found" in text:
                raise DropboxNotFound(from_path) from exc
            if "conflict" in text:
                raise DropboxConflict(text[:200]) from exc
            raise
        return res.get("metadata") or res

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

    def delete_many(self, paths: list[str], wait_s: int = 60) -> int:
        """Delete up to thousands of files in a few calls (one batch job per 1,000) instead of one call each. Returns how
        many were asked for; a path that is already gone is not an error."""
        done = 0
        for i in range(0, len(paths), 1000):
            part = paths[i:i + 1000]
            if len(part) == 1:
                self.delete(part[0])
                done += 1
                continue
            res = self._rpc("files/delete_batch", {"entries": [{"path": p} for p in part]})
            job, t0 = res.get("async_job_id"), time.time()
            while res.get(".tag") == "async_job_id" or res.get(".tag") == "in_progress":
                if time.time() - t0 > wait_s:
                    break       # still running on Dropbox's side: it will finish there; the caller's work is repeatable
                time.sleep(1.0)
                res = self._rpc("files/delete_batch/check", {"async_job_id": job})
            if res.get(".tag") == "failed":
                raise RuntimeError(f"Dropbox files/delete_batch failed: {json.dumps(res)[:200]}")
            done += len(part)
        return done

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


def permanent_download_error(exc: Exception) -> bool:
    """True when asking again cannot help: Dropbox answered with an error about this file itself (it was deleted, is of a
    kind that cannot be downloaded, is restricted). A timeout, a dropped connection or a busy server is not that."""
    if isinstance(exc, DropboxNotFound):
        return True
    return isinstance(exc, RuntimeError) and not isinstance(exc, DropboxAuthError) and " failed 409" in str(exc)


def wanted(entry: dict) -> bool:
    """A file the app reads: a remittance, an 837, one of EMA's exports or reports (Excel, CSV, a zip or a gzip of them).
    The archive of remittances read on a serverless host's first load (REMITWATCH_BACKFILL_ZIP) is not: cloud.ingest_zip
    reads it whole, once."""
    if entry.get(".tag") != "file":
        return False
    name = entry.get("name", "").lower()
    if name.startswith("~$") or name.startswith("."):
        return False
    if name.endswith(".zip") and name == backfill_zip_name().lower():
        return False
    return name.endswith(WANTED_EXT)


def _takes(fn, arg: str) -> bool:
    """Whether a client method takes this keyword (a stand-in client may not)."""
    import inspect
    try:
        return arg in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def read_chunks(client, path_or_id: str, chunk: int = 4 << 20, timeout=REPORT_TIMEOUT) -> Iterator[bytes]:
    """A file's bytes from Dropbox in pieces, with the report read timeout when the client takes one."""
    if _takes(client.download_chunks, "timeout"):
        return client.download_chunks(path_or_id, chunk=chunk, timeout=timeout)
    return client.download_chunks(path_or_id, chunk)


def peek(client, path_or_id: str, n: int = 65536) -> bytes:
    """The first `n` bytes of a file in Dropbox, read in one short request that is then closed (a raw CSV too large to
    download on a serverless host is recognised from these alone)."""
    it = read_chunks(client, path_or_id, chunk=n)
    try:
        return next(iter(it), b"")
    finally:
        close = getattr(it, "close", None)
        if close:
            close()


def local_path(data_dir: str, entry: dict) -> str:
    rel = (entry.get("path_display") or entry.get("path_lower") or entry["name"]).lstrip("/")
    return os.path.join(data_dir, rel)


def _hand_back(exc: BaseException, files: list[str]) -> None:
    """Attach the files a failed sync already downloaded to its exception (exc.new_files), for the caller to read in the
    same cycle (pipeline.run_cycle does). They are in the mirror now, so the next listing calls them unchanged and would
    never hand them over again."""
    try:
        exc.new_files = list(dict.fromkeys(list(files) + list(getattr(exc, "new_files", None) or [])))     # type: ignore[attr-defined]
    except Exception:
        pass


def sync(store, client: DropboxClient | None = None, root_path: str | None = None, data_dir: str | None = None,
         full: bool = False, log=print) -> dict:
    """Mirror new/changed wanted files from Dropbox into data_dir. Returns a summary with the list of new local files.
    When it fails part-way, the files it downloaded before the failure are on the exception, as `new_files`."""
    client = client or DropboxClient()
    root_path = root_path or os.environ.get("DROPBOX_ROOT_PATH", "/Trizetto Data")
    data_dir = data_dir or os.environ.get("REMITWATCH_DATA_DIR", "data/dropbox")
    conn = store.conn
    cursor = None if full else (conn.execute("SELECT value FROM meta WHERE key='sync:cursor'").fetchone() or [None])[0]
    stats = {"listed": 0, "wanted": 0, "downloaded": 0, "unchanged": 0, "errors": 0, "new_files": [], "mode": "delta" if cursor else "full"}
    t0 = time.time()

    def mirror(entries: Iterable[dict]) -> None:
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
                add_pending(conn, [dest])       # kept until a cycle has read it: the position moves past it now
                stats["downloaded"] += 1
                stats["new_files"].append(dest)
                if stats["downloaded"] % 100 == 0:
                    log(f"  downloaded {stats['downloaded']} files")
            except Exception as exc:
                stats["errors"] += 1
                log(f"  ERROR {e.get('path_display')}: {exc}")
    try:
        try:
            mirror(client.list_continue(cursor) if cursor else client.list_folder(root_path, recursive=True))
        except Exception as exc:
            if not (cursor and "reset" in str(exc).lower()):
                raise
            # Dropbox dropped the position: list the whole folder in this same run. What the delta listing already
            # downloaded stays in stats, since the full listing finds it in the mirror and calls it unchanged.
            log("cursor reset by Dropbox; doing a full listing")
            conn.execute("DELETE FROM meta WHERE key='sync:cursor'")
            conn.commit()
            stats["mode"] = "full"
            mirror(client.list_folder(root_path, recursive=True))
        new_cursor = getattr(client, "last_cursor", None)
        if new_cursor:
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('sync:cursor', ?)", (new_cursor,))
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('sync:last_run', ?)", (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),))
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('sync:last_stats', ?)", (json.dumps({k: v for k, v in stats.items() if k != 'new_files'}),))
        conn.commit()
    except Exception as exc:
        # the position saved before this run still stands (a store that could not record the new one also lists the same
        # entries again), so these files would never come back from a listing: the caller reads them in this cycle
        _hand_back(exc, stats["new_files"])
        raise
    stats["seconds"] = round(time.time() - t0, 1)
    return stats


def file_sha256(path: str) -> str:
    """The SHA-256 of a file, read in 1 MB pieces (a report can be hundreds of MB)."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for piece in iter(lambda: fh.read(1 << 20), b""):
            h.update(piece)
    return h.hexdigest()


def _note_report(out: dict, res: dict, name: str, log) -> None:
    """Count what the report reader made of one file (or of each report in a zip)."""
    if res.get("status") == "zip":
        members = res.get("members") or []
        if not members:
            out["skipped"] += 1
            log(f"  {name}: a zip with no EMA report in it; not read")
            return
    else:
        members = [res]
    if any(m.get("status") in ("current", "superseded") for m in members):
        out["ema"] += 1
    if any(m.get("status") not in ("already", "not_report") for m in members):
        out["ema_changed"] = True
    for m in members:
        out.setdefault("ema_reports", []).append({k: m.get(k) for k in ("status", "report", "file_id", "rows_applied")})
    if res.get("status") == "refused" and res.get("code") == "zip_in_zip":
        out["ema_changed"] = True


def _route_report(store, path: str, name: str, *, via: str, origin: dict | None, ledger, deadline, wait_reason, log) -> dict | None:
    """Read a file as one of EMA's two reports when it is one (a zip of reports, a gzip of one, the file itself):
    remitwatch.ema_reports.ingest_file decides and records it. None when it is no report."""
    from . import ema_reports as er

    extra = {"wait_reason": wait_reason} if wait_reason and _takes(er.ingest_file, "wait_reason") else {}
    if er.is_plain_zip(path):
        return er.ingest_file(store, path, via=via, origin=origin, ledger=ledger, deadline=deadline, log=log, **extra)
    try:
        rec = er.recognize(path, name)
    except er.ReportRefused:
        rec = "refused"                 # a report all the same: ingest_file records why it is not read
    if not rec:
        return None
    return er.ingest_file(store, path, None if rec == "refused" else rec, via=via, origin=origin, ledger=ledger,
                          deadline=deadline, log=log, **extra)


def _not_read(store, path: str, kind: str) -> None:
    """A file the router does not read (too large, or none of the files RemitWatch reads): a files row with status
    'not_read', so the Data tab lists it and it is not fetched or read again until it changes."""
    try:
        store.record_file(file_sha256(path), path, kind=kind, status="not_read", lines=0)
    except Exception:       # noqa: BLE001  (the cycle's output still names it)
        pass


# ------------------------------------------------------------------------------- files fetched and not yet read
# The server's Dropbox position moves on as soon as a listing is complete, before the readers take the files it fetched:
# a cycle stopped in between (a deploy restart, the out-of-memory killer) or a read that failed (a busy database, a full
# disk) would leave a file in the mirror that no later listing names again. Each fetched file (and each the one-time
# re-find brought in) is therefore kept in sync_pending until a cycle has offered it to the readers; one whose read raised
# stays, and after PENDING_TRIES cycles it is listed as not read (files kind 'ema:failed') until it changes in Dropbox.
PENDING_DDL = ("CREATE TABLE IF NOT EXISTS sync_pending (path TEXT PRIMARY KEY, added_at TEXT, tries INTEGER NOT NULL "
               "DEFAULT 0)")


def add_pending(conn, paths: Iterable[str], *, commit: bool = True) -> None:
    """These fetched files wait for the readers (a new copy of a file starts its count again)."""
    paths = list(paths)
    if not paths:
        return
    conn.execute(PENDING_DDL)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    conn.executemany("INSERT OR REPLACE INTO sync_pending (path, added_at, tries) VALUES (?, ?, 0)", [(p, now) for p in paths])
    if commit:
        conn.commit()


def take_pending(store, log=print) -> dict:
    """The fetched files a cycle offers the readers again, each counted before it is read (a cycle stopped while reading
    one counts too): {'paths', 'gone', 'given_up'}. A file no longer in the mirror is dropped; one offered PENDING_TRIES
    times is listed as not read."""
    conn = store.conn
    conn.execute(PENDING_DDL)
    take, gone, give_up = [], [], []
    for path, tries in conn.execute("SELECT path, tries FROM sync_pending ORDER BY added_at, path").fetchall():
        if not os.path.isfile(path):
            gone.append(path)
        elif int(tries or 0) >= PENDING_TRIES:
            give_up.append(path)
        else:
            take.append(path)
    for path in give_up:
        _not_read(store, path, "ema:failed")
        log(f"  ERROR {os.path.basename(path)}: not read after {PENDING_TRIES} cycles; it is read again when it changes in "
            "Dropbox")
    conn.executemany("DELETE FROM sync_pending WHERE path=?", [(p,) for p in gone + give_up])
    conn.executemany("UPDATE sync_pending SET tries = tries + 1 WHERE path=?", [(p,) for p in take])
    conn.commit()
    return {"paths": take, "gone": len(gone), "given_up": len(give_up)}


def settle_pending(store, offered: Iterable[str], retry: Iterable[str] = ()) -> int:
    """After the readers: the offered files are done with, save those whose read raised (they are offered again).
    Returns how many still wait."""
    conn = store.conn
    conn.execute(PENDING_DDL)
    keep = set(retry)
    conn.executemany("DELETE FROM sync_pending WHERE path=?", [(p,) for p in set(offered) if p not in keep])
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM sync_pending").fetchone()[0]


def _read_zip_members(store, path: str, names: list[str], *, out: dict, log, origin: dict | None, ledger, via: str,
                      deadline: float | None, wait_reason: str | None, again) -> None:
    """The files in a zip that are not EMA's reports (those were read already): unpacked a piece at a time (at most
    ZIP_CHUNK_BYTES, never past what the zip says each member holds) and each read by the router on its own, under its
    own name and date, so the same bytes are never read twice. A zip inside the zip, or a member larger than any file a
    payer or EMA sends, is listed as not read."""
    import shutil
    import tempfile
    import zipfile

    from . import ema_reports as er

    n = os.path.basename(path)
    try:
        bad = er.zip_refusal(path)
    except Exception:       # noqa: BLE001  (a zip that cannot be opened: below)
        bad = None
    if bad is not None:
        out["errors"] += 1
        out["failed"].append(f"{n} (not read: {bad.message})")
        _not_read(store, path, "ema:too_large")
        return
    try:
        zf = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile):
        out["errors"] += 1
        out["failed"].append(f"{n} (not read: not a zip that opens)")
        _not_read(store, path, "ema:unknown")
        return
    want = set(names)
    d = tempfile.mkdtemp(prefix="unzip-", dir=_scratch_dir())
    chunk: list[str] = []
    member_of: dict[str, str] = {}
    size = 0
    read = 0

    def flush() -> None:
        nonlocal size
        if not chunk:
            return
        origins = {q: dict(origin or {}, member=member_of[q]) for q in chunk}
        part = ingest_new_files(store, list(chunk), log=log, origins=origins, ledger=ledger, via=via, deadline=deadline,
                                wait_reason=wait_reason, _in_zip=True)
        for k in ("remits", "ema", "claims_837", "skipped", "errors"):
            out[k] = out.get(k, 0) + int(part.get(k) or 0)
        out["failed"] += [f"{n}: {f}" for f in part.get("failed") or []]
        for k in ("ema_reports", "ema_files"):
            if part.get(k):
                out.setdefault(k, []).extend(part[k])
        if part.get("ema_changed"):
            out["ema_changed"] = True
        if part.get("retry"):
            again(path)                     # a member's read raised: the whole zip is offered again (read members are skipped)
        for q in chunk:
            shutil.rmtree(os.path.dirname(q), ignore_errors=True)
        chunk.clear()
        size = 0
    try:
        for k, info in enumerate(i for i in zf.infolist() if i.filename in want):
            base = os.path.basename(info.filename.rstrip("/")) or "file"
            low = base.lower()
            if low.endswith(".zip"):
                out["errors"] += 1
                out["failed"].append(f"{n}: {base} (a zip inside a zip is not read)")
                continue
            if not low.endswith(WANTED_EXT):
                out["skipped"] += 1
                continue
            if info.file_size > max(MAX_X12_BYTES, MAX_EXPORT_BYTES):
                out["errors"] += 1
                out["failed"].append(f"{n}: {base} (too large)")
                continue
            sub = os.path.join(d, f"{k:05d}")
            os.makedirs(sub, exist_ok=True)
            q = os.path.join(sub, base)
            with zf.open(info) as src, open(q, "wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
            try:
                when = time.mktime(tuple(info.date_time) + (0, 0, -1))
                os.utime(q, (when, when))       # the member's own date: the newest copy of a payment twice sent wins
            except (OverflowError, ValueError, OSError):
                pass
            chunk.append(q)
            member_of[q] = info.filename
            size += info.file_size
            read += 1
            if size >= ZIP_CHUNK_BYTES:
                flush()
        flush()
    finally:
        zf.close()
        shutil.rmtree(d, ignore_errors=True)
    log(f"  {n}: a zip of {read} file{'s' if read != 1 else ''}, each read on its own")


def _gunzip_to(path: str, folder: str, limit: int) -> str | None:
    """A gzipped file that is not a report, unpacked under its own name (less .gz) into `folder`; None when it unpacks to
    more than `limit` bytes (nothing but a report is that large) or is not gzip at all."""
    import gzip
    import zlib
    name = os.path.basename(path)[:-3] or "file"
    out = os.path.join(folder, name)
    n = 0
    try:
        with gzip.open(path, "rb") as src, open(out, "wb") as dst:
            for piece in iter(lambda: src.read(1 << 20), b""):
                n += len(piece)
                if n > limit:
                    break
                dst.write(piece)
    except (OSError, EOFError, zlib.error):
        n = limit + 1
    if n > limit:
        try:
            os.remove(out)
        except OSError:
            pass
        return None
    return out


def ingest_new_files(store, paths: list[str], log=print, unread: list[str] | None = None, *, origins: dict | None = None,
                     ledger=None, via: str = "mirror", deadline: float | None = None, wait_reason: str | None = None,
                     _in_zip: bool = False) -> dict:
    """Route files to the right reader, EMA's two reports first. Each .xlsx, .csv, .tsv, .txt, .zip or .gz is asked
    whether it is the A/R or the Production report (remitwatch.ema_reports); a report goes to ema_reports.ingest_file,
    with its origin (`origins`, by path) and the ledger opener (`ledger`: a Ledger or a callable; without one a
    Production file waits). Otherwise a spreadsheet is read as one of the three weekly exports (one over 60 MB is refused
    before it is read), an X12 file as a remittance or an 837. A spreadsheet that is none of them is recorded as not read
    (files kind 'ema:unknown'), so it is listed on the Data tab and not fetched again. `via` labels where the files came
    from ('mirror' on a server, 'dropbox' from cloud, 'upload', 'cli'). `unread` is kept for callers that pass it: no file
    is left unread now. A zip of other files (remittances, exports) has each member read on its own. Returns counts;
    'ema_reports' lists what the report reader made of each report, 'retry' the files whose read raised (a busy database,
    a full disk), for the server to offer again (sync_pending)."""
    import shutil
    import tempfile

    from .ema_exports import TEXT_EXPORTS, IsReport, TooLarge, export_created, iter_rows
    from .x12_835 import looks_like_x12

    out = {"remits": 0, "ema": 0, "claims_837": 0, "skipped": 0, "errors": 0, "failed": []}
    origins = origins or {}
    remits: list[str] = []
    scratch: list[str] = []
    queue = list(paths)
    root_of: dict[str, str] = {}        # a file unpacked from a gzip -> the file it came from
    retry: list[str] = []               # files whose read raised: offered again by the next cycle (server: sync_pending)

    def again(path: str) -> None:
        q = root_of.get(path, path)
        if q not in retry:
            retry.append(q)
    try:
        while queue:
            p = queue.pop(0)
            n = os.path.basename(p)
            low = n.lower()
            if low.endswith(REPORT_EXTS):
                try:
                    res = _route_report(store, p, n, via=via, origin=origins.get(p), ledger=ledger, deadline=deadline,
                                        wait_reason=wait_reason, log=log)
                except Exception as exc:        # noqa: BLE001  (never the message: a converter's message can hold a cell)
                    out["errors"] += 1
                    out["failed"].append(n)
                    again(p)
                    log(f"  EMA report ERROR {n}: {type(exc).__name__}")
                    continue
                if res is not None:
                    others = res.get("others") if res.get("status") == "zip" else None
                    if others and not _in_zip:
                        # a zip of other files (a week of remittances, the weekly exports), not only of EMA's reports:
                        # each member is read on its own, the same bytes never twice (the first-load archive of
                        # remittances is not this: cloud.ingest_zip reads it, once)
                        if res.get("members"):
                            _note_report(out, res, n, log)
                        _read_zip_members(store, p, others, out=out, log=log, origin=origins.get(p), ledger=ledger,
                                          via=via, deadline=deadline, wait_reason=wait_reason, again=again)
                    else:
                        _note_report(out, res, n, log)
                    continue
                if low.endswith(".zip"):
                    out["skipped"] += 1
                    log(f"  {n}: not read (only a zip of EMA reports is read here)")
                    continue
                if low.endswith(".gz"):
                    d = tempfile.mkdtemp(prefix="gunzip-", dir=_scratch_dir())
                    scratch.append(d)
                    inner = _gunzip_to(p, d, MAX_EXPORT_BYTES)
                    if inner is None:
                        # listed on the Data tab (not read), never skipped in silence; not fetched again until it changes
                        out["errors"] += 1
                        out["failed"].append(f"{n} (not read: not a report, and too large or not gzip)")
                        _not_read(store, p, "ema:too_large")
                        continue
                    if p in origins:
                        origins[inner] = origins[p]
                    root_of[inner] = root_of.get(p, p)
                    queue.insert(0, inner)
                    continue
            if low.endswith((".xlsx",) + TEXT_EXPORTS):
                try:
                    sha = file_sha256(p)
                    if store.file_seen(sha):
                        out["skipped"] += 1
                        continue
                    if os.path.getsize(p) > MAX_EXPORT_BYTES:
                        # one of the weekly exports is read whole in memory: one this large is not read at all
                        out["errors"] += 1
                        out["failed"].append(f"{n} (too large for this report)")
                        store.record_file(sha, p, kind="ema:too_large", status="not_read", lines=0)
                        log(f"  EMA ERROR {n}: Not read: an export over {MAX_EXPORT_BYTES >> 20} MB. Export a shorter date range.")
                        continue
                    acct: dict = {}
                    it = iter_rows(p, acct)
                    first = next(it, None)          # reads the file and its column names (acct) before any row is converted
                    if (acct.get("family") or "unknown") == "unknown":
                        # none of EMA's exports or reports: no row is stored, and the file is noted as not read, so the
                        # Data tab lists it and it is not fetched again until it changes
                        out["skipped"] += 1
                        store.record_file(sha, p, kind="ema:unknown", status="not_read", lines=0)
                        log(f"  EMA ERROR {n}: Not read: it is none of the EMA exports or reports RemitWatch reads "
                            "(A/R report, Production report, Encounter & Charge Lines, ERA & Payment Lines, Claim & Claim "
                            "Status). Nothing from it is stored.")
                        continue
                    rows = ([first] if first is not None else []) + list(it)
                    r = store.ingest_ema_rows(rows)
                    left = {k: v for k, v in (acct.get("left") or {}).items() if v}
                    if left or acct.get("above"):
                        log(f"  EMA {n}: {acct.get('above', 0)} line(s) above the column names left out"
                            + "".join(f", {v} {k} line(s) left out" for k, v in left.items()))
                    fam = rows[0]["family"] if rows else (acct.get("family") or "unknown")
                    store.record_file(sha, p, kind=f"ema:{fam}", status="ok", lines=len(rows), duplicates=len(rows) - r["inserted"])
                    store.record_export(n, sha, fam, export_created(p), len(rows))
                    out["ema"] += 1
                    out.setdefault("ema_files", []).append(_export_summary(n, rows, acct, fam, int(r["inserted"])))
                except IsReport as exc:
                    out["errors"] += 1
                    out["failed"].append(n)
                    log(f"  EMA ERROR {n}: {exc}")
                except TooLarge as exc:
                    # a workbook that unpacks to far more than any export (or holds more than is read at once): listed on
                    # the Data tab as not read, and not fetched again until it changes
                    out["errors"] += 1
                    out["failed"].append(f"{n} (too large)")
                    _not_read(store, p, "ema:too_large")
                    log(f"  EMA ERROR {n}: {exc}")
                except Exception as exc:        # noqa: BLE001  (never the message: a converter's message can hold a cell)
                    out["errors"] += 1
                    out["failed"].append(n)
                    again(p)
                    log(f"  EMA ERROR {n}: {type(exc).__name__}")
            elif looks_like_x12(p):
                if os.path.getsize(p) > MAX_X12_BYTES:
                    out["errors"] += 1
                    out["failed"].append(f"{n} (too large)")
                    _not_read(store, p, "ema:too_large")
                    log(f"  ERROR {n}: Not read: a remittance or claim file over {MAX_X12_BYTES >> 20} MB is no file a payer "
                        "sends.")
                    continue
                with open(p, "rb") as fh:
                    head = fh.read(400)
                if b"*837*" in head or b"GS*HC*" in head:
                    try:
                        from .x12_837 import ingest_837_file
                        ingest_837_file(store, p)
                        out["claims_837"] += 1
                    except Exception as exc:
                        out["errors"] += 1
                        out["failed"].append(n)
                        again(p)
                        log(f"  837 ERROR {n}: {type(exc).__name__}")
                else:
                    remits.append(p)
            else:
                out["skipped"] += 1
        if remits:
            st = store.ingest_835_paths(remits)
            out["remits"] += st["ingested"]
            out["remit_stats"] = st
            if st.get("transactions"):
                mark_map_dirty(store.conn)      # committed with the next write below
            store.mark_superseded()
    finally:
        for d in scratch:
            shutil.rmtree(d, ignore_errors=True)
    if retry:
        out["retry"] = retry
    return out


REFIND_KEY = "ema:refind_v1"        # set once the first cycle that reads EMA's reports has looked for the ones it passed


def _export_summary(name: str, rows: list[dict], acct: dict, family: str, added: int) -> dict:
    """What one weekly export held and what of it was added, in the shape the Upload page words (uploads.describe): an
    upload read through the intake hand-off says the same as one read in one piece. Counts and dates only."""
    days = [r["service_date"] for r in rows if r.get("service_date")]
    distinct = len({r.get("row_fingerprint") for r in rows}) if rows else 0
    titles = {"charge_lines": "Encounter & Charge Lines", "era_lines": "ERA & Payment Lines", "claim_status": "Claim & Claim Status"}
    return {"kind": "ema", "family": family, "report": titles.get(family, family), "format": acct.get("format"),
            "rows": len(rows), "claims": len({r["claim_id"] for r in rows if r.get("claim_id")}),
            "service_from": min(days) if days else None, "service_to": max(days) if days else None,
            "added": added, "already": max(0, distinct - added), "repeats": len(rows) - distinct,
            "left": {k: v for k, v in (acct.get("left") or {}).items() if v}, "above": acct.get("above", 0),
            "read_at": time.strftime("%Y-%m-%d", time.gmtime()), "name": name}


def unread_here(store, sha: str) -> bool:
    """A file of these bytes was never taken in: no files row and no report catalog row has its SHA-256."""
    if store.file_seen(sha):
        return False
    try:
        return store.conn.execute("SELECT 1 FROM ema_report_files WHERE file_id=?", (sha,)).fetchone() is None
    except Exception:       # noqa: BLE001
        return True


def refind_reports(store, client: DropboxClient | None = None, root_path: str | None = None, data_dir: str | None = None,
                   log=print) -> list[str]:
    """Once per server (meta ema:refind_v1): the Dropbox position moved past files an older build did not read (EMA's
    A/R and Production reports: a .csv was not even fetched, an A/R .xlsx was fetched and left unread), so no listing of
    what changed names them again. List the whole folder (every page), bring every file of a report's kind (.xlsx,
    .csv, .tsv, .zip, .gz) into the mirror, and hand back those whose bytes were never taken in, for the router to
    recognise and read. The backfill zip and the state folder stay out."""
    conn = store.conn
    if conn.execute("SELECT 1 FROM meta WHERE key=?", (REFIND_KEY,)).fetchone():
        return []
    client = client or DropboxClient()
    root_path = root_path or os.environ.get("DROPBOX_ROOT_PATH", "/Trizetto Data")
    data_dir = data_dir or os.environ.get("REMITWATCH_DATA_DIR", "data/dropbox")
    found: list[str] = []
    for e in client.list_folder(root_path, recursive=True):
        name = (e.get("name") or "").lower()
        if not wanted(e) or not name.endswith(tuple(x for x in REPORT_EXTS if x != ".txt")):
            continue
        dest = local_path(data_dir, e)
        if not (os.path.exists(dest) and os.path.getsize(dest) == e.get("size")
                and (not e.get("content_hash") or dropbox_content_hash(dest) == e["content_hash"])):
            client.download(e.get("id") or e["path_lower"], dest)
        if unread_here(store, file_sha256(dest)):
            found.append(dest)
    # pending in the same commit as the flag: a cycle stopped before the readers took them offers them again
    add_pending(conn, found, commit=False)
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (REFIND_KEY, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
    conn.commit()
    if found:
        log(f"  {len(found)} file(s) in the data folder an older build passed without reading: read now")
    return found


def _scratch_dir() -> str:
    from . import runtime
    return runtime.tmp_dir()


def mark_map_dirty(conn) -> bool:
    """New remittances arrived: EMA's claim-link map is made again by the next detection (Part B), so EMA postings the
    new 835s carry are marked. Only when the store holds an EMA report read as one (otherwise there is nothing to map).
    Not committed here."""
    try:
        if not conn.execute("SELECT 1 FROM ema_report_files WHERE status IN ('current','partly_replaced','replaced','superseded') "
                            "LIMIT 1").fetchone():
            return False
    except Exception:       # noqa: BLE001  (a store with no report table yet has nothing to map)
        return False
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:map_dirty', '1')")
    return True
