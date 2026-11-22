"""Dropbox sync logic with a fake client (no network)."""
from __future__ import annotations

import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch.dropbox_sync import dropbox_content_hash, ingest_new_files, local_path, sync, wanted  # noqa: E402
from remitwatch.store import Store  # noqa: E402


class FakeClient:
    def __init__(self, files: dict[str, bytes]):
        self.files = files
        self.calls: list[str] = []
        self.last_cursor = None

    def _entries(self):
        for path, data in self.files.items():
            yield {".tag": "file", "name": os.path.basename(path), "path_lower": path.lower(), "path_display": path,
                   "id": "id:" + hashlib.md5(path.encode()).hexdigest()[:8], "size": len(data), "content_hash": _dbx_hash(data)}
        yield {".tag": "folder", "name": "sub", "path_lower": "/trizetto data/sub", "path_display": "/Trizetto Data/sub"}

    def list_folder(self, path, recursive=True):
        self.calls.append("list_folder")
        yield from self._entries()
        self.last_cursor = "cursor-1"

    def list_continue(self, cursor):
        self.calls.append("list_continue:" + cursor)
        yield from self._entries()
        self.last_cursor = "cursor-2"

    def download(self, path_or_id, dest):
        self.calls.append("download")
        data = next(v for k, v in self.files.items() if path_or_id in ("id:" + hashlib.md5(k.encode()).hexdigest()[:8], k.lower()))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as fh:
            fh.write(data)
        return {}


def _dbx_hash(data: bytes) -> str:
    block = 4 * 1024 * 1024
    return hashlib.sha256(b"".join(hashlib.sha256(data[i:i + block]).digest() for i in range(0, max(len(data), 1), block))).hexdigest()


def test_wanted_and_local_path():
    assert wanted({".tag": "file", "name": "P39026D999999999U835747-123456789-UMR-999999999.RMT"})
    assert wanted({".tag": "file", "name": "Encounter & Charges 9-14-26.xlsx"})
    assert wanted({".tag": "file", "name": "PROFESSIONAL_Claims_20261009180510714.txt"})
    assert not wanted({".tag": "file", "name": "Trizetto.zip"})
    assert not wanted({".tag": "folder", "name": "Reports"})
    assert not wanted({".tag": "file", "name": ".DS_Store"})
    assert local_path("data/dropbox", {"path_display": "/Trizetto Data/Reports/a.xlsx", "name": "a.xlsx"}) == os.path.join("data/dropbox", "Trizetto Data/Reports/a.xlsx")


def test_content_hash_matches_dropbox_algorithm(tmp_path):
    p = tmp_path / "f.bin"
    data = os.urandom(5 * 1024 * 1024 + 123)
    p.write_bytes(data)
    assert dropbox_content_hash(str(p)) == _dbx_hash(data)
    real = os.environ.get("REMITWATCH_HASH_CHECK_FILE")
    if real and os.path.exists(real):
        assert dropbox_content_hash(real) == os.environ["REMITWATCH_HASH_CHECK_VALUE"]


def test_sync_delta_and_skip_unchanged(tmp_path):
    from tests.test_remitwatch import CLAIM_DOWNCODE, remit

    files = {"/Trizetto Data/Trizetto/a.RMT": remit(CLAIM_DOWNCODE, "77.60", seg_count=29), "/Trizetto Data/Trizetto.zip": b"zip", "/Trizetto Data/notes.docx": b"doc"}
    client = FakeClient(files)
    store = Store(str(tmp_path / "s.sqlite"))
    stats = sync(store, client=client, root_path="/Trizetto Data", data_dir=str(tmp_path / "mirror"), log=lambda *a: None)
    assert stats["mode"] == "full" and stats["downloaded"] == 1 and stats["wanted"] == 1 and stats["errors"] == 0
    assert os.path.exists(tmp_path / "mirror" / "Trizetto Data" / "Trizetto" / "a.RMT")
    assert store.query("SELECT value FROM meta WHERE key='sync:cursor'")[0]["value"] == "cursor-1"
    r = ingest_new_files(store, stats["new_files"], log=lambda *a: None)
    assert r["remits"] == 1
    # second run: delta listing, nothing changed -> nothing downloaded
    stats2 = sync(store, client=client, root_path="/Trizetto Data", data_dir=str(tmp_path / "mirror"), log=lambda *a: None)
    assert stats2["mode"] == "delta" and stats2["downloaded"] == 0 and stats2["unchanged"] == 1
    assert client.calls.count("download") == 1 and any(c.startswith("list_continue:cursor-1") for c in client.calls)
    assert store.query("SELECT value FROM meta WHERE key='sync:cursor'")[0]["value"] == "cursor-2"


def test_report_files_are_mirrored_and_routed_and_the_backfill_archive_stays_out(tmp_path, monkeypatch):
    """A server's mirror now takes EMA's reports (a CSV, a zip of one) and the router reads them first; the archive of
    remittances read on a serverless first load stays out; a CSV that is no report and no export is noted, not loaded."""
    import datetime as dt

    from remitwatch import ema_reports as er
    from tests import ema_fixtures as F
    monkeypatch.setattr(er, "CLAIM_HOST_SUFFIX", F.HOST_SUFFIX)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    monkeypatch.delenv("REMITWATCH_EMA_REPORTS", raising=False)
    ar = F.ar_xlsx([F.ar_row(svc=dt.date(2026, 10, 5))])
    files = {"/Trizetto Data/Reports/2026 Production.csv": F.production_csv([F.production_row()]),
             "/Trizetto Data/Reports/AR 2026.zip": F.zipped("AR 2026.xlsx", ar),
             "/Trizetto Data/Trizetto.zip": b"zip", "/Trizetto Data/notes.csv": b"a,b\n1,2\n"}
    client = FakeClient(files)
    store = Store(str(tmp_path / "s.sqlite"))
    stats = sync(store, client=client, root_path="/Trizetto Data", data_dir=str(tmp_path / "mirror"), log=lambda *a: None)
    assert stats["downloaded"] == 3 and not os.path.exists(tmp_path / "mirror" / "Trizetto Data" / "Trizetto.zip")
    out = ingest_new_files(store, stats["new_files"], log=lambda *a: None)
    got = {r[0]: r[1] for r in store.conn.execute("SELECT report, status FROM ema_report_files")}
    assert got == {"ar_report": "current", "production": "waiting"}          # Production waits for the cycle's ledger
    notes = hashlib.sha256(files["/Trizetto Data/notes.csv"]).hexdigest()
    assert tuple(store.conn.execute("SELECT kind, status FROM files WHERE sha256=?", (notes,)).fetchone()) == ("ema:unknown", "not_read")
    assert store.conn.execute("SELECT COUNT(*) FROM ema_rows").fetchone()[0] == 0 and out["skipped"] >= 1
