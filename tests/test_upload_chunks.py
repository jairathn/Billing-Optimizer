"""
Large files sent in pieces, and the checklist of the five EMA exports (remitwatch.api_uploads, remitwatch.uploads and
remitwatch/static/upload.js), on both hosts: a server keeps the pieces beside its store and hands the finished file to
<data_dir>/incoming/ready/; a serverless host relays each piece to a Dropbox upload session (here tests.fakes.FakeDropbox,
which answers the upload-session calls as Dropbox does) or, with a local state folder, keeps them there, and hands the
file to the state folder's intake/. Each test runs on all three ("server", "local", "dropbox") unless it says otherwise.

Every file is invented: report rows from tests/ema_fixtures.py, or bytes made up on the spot.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import os
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import types
import zlib
from datetime import date, datetime, timedelta, timezone

import pytest

from remitwatch import api_uploads, uploads
from tests import ema_fixtures as F
from tests.fakes import FakeDropbox

XHR = {"X-Requested-With": "remitwatch"}
MODES = ("server", "local", "dropbox")
NAME = "2026 Production(Sheet1).csv"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOAD_JS = os.path.join(ROOT, "remitwatch", "static", "upload.js")


def text_report(lines: int = 4000, seed: int = 1) -> bytes:
    """A made-up text export: a row of column names and lines of invented values (hex, so it compresses about two to one)."""
    rnd = random.Random(seed)
    head = ",".join(F.PRODUCTION_COLUMNS[:6]) + "\r\n"
    return (head + "".join(f"{rnd.getrandbits(64):016x},{i},{rnd.getrandbits(32):08x},,,\r\n" for i in range(lines))).encode()


class Site:
    """The dashboard on one host, signed in (an open developer's machine: the user is 'anonymous'), with what the test
    needs to look behind it."""

    def __init__(self, mode, tmp, c, db, state=None, fake=None, triggered=None, kicked=None, copy=None):
        self.mode, self.tmp, self.c, self.db, self.state, self.fake = mode, tmp, c, db, state, fake
        self.triggered, self.kicked, self.copy = triggered, kicked, copy

    def store(self):
        return uploads.ServerUploads(self.db) if self.mode == "server" else uploads.state_uploads(self.state)

    def intake_dir(self, *parts: str) -> str:
        if self.mode == "server":
            return os.path.join(os.path.dirname(self.db), "incoming", "ready", *parts)
        if self.mode == "local":
            return os.path.join(self.state.root, "intake", *parts)
        return os.path.join(self.fake.root, "RemitWatch-state", "intake", *parts)

    def intake(self) -> dict:
        """The files directly in the intake folder (not its done/ folder), by name."""
        d = self.intake_dir()
        if not os.path.isdir(d):
            return {}
        out = {}
        for f in sorted(os.listdir(d)):
            p = os.path.join(d, f)
            if os.path.isfile(p):
                with open(p, "rb") as fh:
                    out[f] = fh.read()
        return out

    def done(self, pair: str, sidecar: dict) -> None:
        """What the cycle does once it has read a pair: the data file to done/ first, then the sidecar, with its result."""
        src = self.intake_dir()
        dst = os.path.join(src, "done") if self.mode != "server" else os.path.join(os.path.dirname(src), "done")
        os.makedirs(dst, exist_ok=True)
        os.replace(os.path.join(src, pair), os.path.join(dst, pair))
        os.remove(os.path.join(src, pair + ".json"))
        with open(os.path.join(dst, pair + ".json"), "w") as fh:
            json.dump(sidecar, fh)

    def put_serving(self, fill) -> None:
        """A dashboard copy of the store (serverless) holding what `fill` writes, as the run would have saved it."""
        from remitwatch import cloud, ema_reports
        p = str(self.tmp / "build-serving.sqlite")
        cloud.empty_serving(p)
        c = sqlite3.connect(p)
        ema_reports.ensure_schema(c)
        fill(c)
        c.commit()
        c.close()
        with open(p, "rb") as fh:
            self.state.put(cloud.SERVING, gzip.compress(fh.read()))
        self.copy.checked = 0.0

    def fill(self, fill) -> None:
        """Write into what this host's routes read: the store (server) or the dashboard copy (serverless)."""
        if self.mode == "server":
            from remitwatch import ema_reports
            c = sqlite3.connect(self.db)
            ema_reports.ensure_schema(c)
            fill(c)
            c.commit()
            c.close()
        else:
            self.put_serving(fill)


@pytest.fixture
def build(tmp_path, monkeypatch):
    """A Site on one host, with pieces of `chunk` bytes (small, so a test file of a few hundred KB has several)."""
    from fastapi.testclient import TestClient

    import remitwatch.api as api
    from remitwatch import cloud
    from remitwatch.state import DropboxState, LocalState

    def make(mode: str, chunk: int = 16 * 1024) -> Site:
        monkeypatch.setattr(uploads, "CHUNK_BYTES", chunk)
        db = str(tmp_path / "data" / "s.sqlite")
        os.makedirs(os.path.dirname(db), exist_ok=True)
        api.ensure_server_schema(db)
        monkeypatch.setattr(api, "DB_PATH", db)
        monkeypatch.setattr(api, "PASSWORD", "")
        monkeypatch.setattr(api, "SHOW_PHI", False)
        monkeypatch.setenv("REMITWATCH_ALLOW_OPEN", "1")
        monkeypatch.setenv("REMITWATCH_TMP", str(tmp_path / "tmp"))
        for k in ("VERCEL", "VERCEL_ENV", "REMITWATCH_STATE_DIR", "REMITWATCH_UPLOAD_MAX_GB", "DROPBOX_APP_KEY", "DROPBOX_APP_SECRET",
                  "DROPBOX_REFRESH_TOKEN", "CRON_SECRET"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setattr(api_uploads, "_SWEPT", {"at": time.time()})      # no clean-up unless a test asks for one
        kicked: list = []
        monkeypatch.setattr(uploads, "kick_intake", lambda db_path: kicked.append(db_path) or False)
        state = fake = copy = None
        triggered: list = []
        if mode == "server":
            monkeypatch.delenv("REMITWATCH_MODE", raising=False)
        else:
            monkeypatch.setenv("REMITWATCH_MODE", "serverless")
            if mode == "local":
                state = LocalState(str(tmp_path / "state"))
            else:
                fake = FakeDropbox(str(tmp_path / "dropbox"))
                state = DropboxState(fake, "/RemitWatch-state")
            copy = cloud.ServingCopy()
            copy.state = lambda fast=False: state
            monkeypatch.setattr(cloud, "SERVING_COPY", copy)
            monkeypatch.setattr(cloud, "trigger_cycle", lambda base, trigger="webhook", delay=0, **kw: triggered.append(trigger) or True)
        return Site(mode, tmp_path, TestClient(api.app), db, state, fake, triggered, kicked, copy)
    return make


@pytest.fixture(params=MODES)
def site(request, build) -> Site:
    return build(request.param)


class Up:
    """One file sent the way the page sends it: start, the pieces in order, finish."""

    def __init__(self, site: Site, raw: bytes, *, name: str = NAME, gz: bool = True, hint: str | None = "production", sha: bool = True):
        self.site, self.raw, self.name, self.gz, self.hint, self.give_sha = site, raw, name, gz, hint, sha
        self.sha = hashlib.sha256(raw).hexdigest()
        self.body = gzip.compress(raw, 6) if gz else raw
        n = uploads.CHUNK_BYTES
        self.pieces = [self.body[i:i + n] for i in range(0, len(self.body), n)]
        self.uid = None

    def start(self, **extra):
        body = {"name": self.name, "size": len(self.raw), "gz": self.gz, "report_hint": self.hint}
        if self.give_sha:
            body["sha256"] = self.sha
        r = self.site.c.post("/api/uploads/start", json=dict(body, **extra), headers=XHR)
        if r.status_code == 200 and r.json().get("upload_id"):
            self.uid = r.json()["upload_id"]
        return r

    def total(self, i: int) -> int:
        return len(self.pieces) if (not self.gz or i == len(self.pieces) - 1) else 0

    def piece(self, i: int, data: bytes | None = None, sha: str | None = None, total: int | None = None, headers=XHR):
        data = self.pieces[i] if data is None else data
        return self.site.c.post("/api/uploads/chunk", content=data, headers=headers,
                                params={"upload_id": self.uid, "index": i, "total": self.total(i) if total is None else total,
                                        "sha256": sha or hashlib.sha256(data).hexdigest()})

    def finish(self, sha: str | None = None, chunks: int | None = None, nbytes: int | None = None):
        return self.site.c.post(f"/api/uploads/{self.uid}/finish", headers=XHR,
                                json={"sha256": sha or self.sha, "chunks": len(self.pieces) if chunks is None else chunks,
                                      "bytes": len(self.body) if nbytes is None else nbytes})

    def send(self, start: bool = True):
        if start:
            r = self.start()
            assert r.status_code == 200 and r.json()["status"] in ("started", "resume"), r.text
        for i in range(len(self.pieces)):
            r = self.piece(i)
            assert r.status_code == 200, (i, r.text)
        return self.finish()


def status(site: Site, uid: str) -> dict:
    r = site.c.get(f"/api/uploads/{uid}/status")
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------------------------------------- the hand-off
@pytest.mark.parametrize("gz", [True, False])
def test_a_file_sent_in_pieces_lands_in_the_intake_folder_with_its_exact_sidecar(site, gz):
    raw = text_report()
    up = Up(site, raw, gz=gz)
    assert len(up.pieces) >= 3
    t0 = datetime.now(timezone.utc).replace(microsecond=0)
    r = up.send()
    assert r.status_code == 200, r.text
    fin = r.json()
    assert fin["state"] == "finished" and fin["received"] == len(up.pieces) and fin["bytes"] == len(up.body)
    assert any(line.startswith("Received and checked on") for line in fin["lines"])
    pair = f"{up.uid}__{NAME}" + (".gz" if gz else "")
    got = site.intake()
    assert sorted(got) == [pair, pair + ".json"]
    assert got[pair] == up.body                                  # the bytes as sent: compressed when the page compressed them
    assert (gzip.decompress(got[pair]) if gz else got[pair]) == raw
    sidecar = json.loads(got[pair + ".json"])
    assert list(sidecar) == list(uploads.SIDECAR_KEYS)
    received = datetime.fromisoformat(sidecar.pop("received_at"))
    assert t0 - timedelta(seconds=1) <= received <= datetime.now(timezone.utc) + timedelta(seconds=1)
    assert sidecar == {"name": NAME, "origin": "upload", "user": "anonymous", "sha256": up.sha, "size": len(raw), "gz": gz,
                       "report_hint": "production"}
    if site.mode != "dropbox":                                   # owner-only, like the store
        for f in (pair, pair + ".json"):
            assert os.stat(site.intake_dir(f)).st_mode & 0o777 == 0o600
    if site.mode == "server":                                    # nothing of the upload is left in the working folder
        assert not [f for f in os.listdir(os.path.dirname(site.intake_dir())) if f.endswith(".part")]
        assert site.kicked == [site.db] and "The next cycle reads it." in fin["lines"]
    else:
        assert site.triggered == ["upload"] and "The next run was asked to start now; it reads the file first." in fin["lines"]
    st = status(site, up.uid)
    assert st["state"] == "finished" and st["chunks"] == [hashlib.sha256(p).hexdigest() for p in up.pieces]
    assert st["name"] == NAME and st["report_hint"] == "production"


def test_every_route_needs_the_sign_in_and_the_header(site, monkeypatch):
    import remitwatch.api as api
    up = Up(site, text_report(300))
    assert up.start().status_code == 200
    # without the header a cross-site form cannot set, nothing that changes something is taken
    assert site.c.post("/api/uploads/start", json={"name": "a.csv", "size": 3}).status_code == 400
    assert up.piece(0, headers={}).status_code == 400
    assert site.c.post(f"/api/uploads/{up.uid}/finish", json={}).status_code == 400
    assert site.c.put("/api/ema/exports/ar/howto", json={"text": "x"}).status_code == 400
    monkeypatch.setattr(api, "PASSWORD", "correct-horse-battery-7")
    for method, path, kw in (("post", "/api/uploads/start", {"json": {"name": "a.csv", "size": 3}}),
                             ("post", "/api/uploads/chunk", {"params": {"upload_id": up.uid, "index": 0, "sha256": "0" * 64}, "content": b"x"}),
                             ("get", f"/api/uploads/{up.uid}/status", {}), ("post", f"/api/uploads/{up.uid}/finish", {"json": {}}),
                             ("get", "/api/uploads/recent", {}), ("get", "/api/ema/exports", {}), ("get", "/api/ema/exports/ar/howto", {}),
                             ("put", "/api/ema/exports/ar/howto", {"json": {"text": "x"}})):
        r = getattr(site.c, method)(path, headers=XHR, **kw)
        assert r.status_code == 401, (path, r.status_code)
    r = site.c.get("/api/ema/exports", auth=("oda", "correct-horse-battery-7"))
    assert r.status_code in (200, 401)              # the shared login's name is the configured one
    monkeypatch.setattr(api, "USERNAME", "staff")
    assert site.c.get("/api/ema/exports", auth=("staff", "correct-horse-battery-7")).status_code == 200


# ------------------------------------------------------------------------------------------- breaks and resumes
def test_a_lost_piece_is_sent_again_and_the_upload_carries_on(site):
    raw = text_report(6000)
    up = Up(site, raw, gz=True)
    n = len(up.pieces)
    assert n >= 4
    k = n // 2
    up.start()
    for i in range(k):
        assert up.piece(i).status_code == 200
    # the page went away after piece k: what the server has says where to carry on
    st = status(site, up.uid)
    assert st["state"] == "receiving" and st["received"] == k and st["next_index"] == k
    assert st["chunks"] == [hashlib.sha256(p).hexdigest() for p in up.pieces[:k]]
    assert any("Choose the same file again to carry on" in line for line in st["lines"])
    # the same file chosen again (a new page): the upload carries on, under the same id
    again = Up(site, raw, gz=True)
    r = again.start()
    assert r.status_code == 200 and r.json()["status"] == "resume" and r.json()["upload_id"] == up.uid and r.json()["next_index"] == k
    # a piece whose answer was lost, sent again: taken once
    r = up.piece(k - 1)
    assert r.status_code == 200 and r.json()["again"] is True and r.json()["received"] == k
    # a piece out of order: refused, with the piece the upload waits for
    r = up.piece(k + 1)
    assert r.status_code == 409 and r.json()["next_index"] == k
    for i in range(k, n):
        assert up.piece(i).status_code == 200
    assert up.finish().status_code == 200
    pair = f"{up.uid}__{NAME}.gz"
    assert gzip.decompress(site.intake()[pair]) == raw
    # asking for a fresh upload of the same file is possible, and finishing it says RemitWatch has it already
    fresh = Up(site, raw, gz=True)
    r = fresh.start(new=True)
    assert r.status_code == 200 and r.json()["status"] == "already"


def test_a_tampered_piece_is_refused_and_changes_nothing(site):
    up = Up(site, text_report(), gz=False)
    up.start()
    assert up.piece(0).status_code == 200
    good = up.pieces[1]
    bad = bytes([good[0] ^ 1]) + good[1:]
    r = up.piece(1, data=bad, sha=hashlib.sha256(good).hexdigest())      # the checksum of the bytes meant, not of those sent
    assert r.status_code == 422 and "did not arrive whole" in r.json()["detail"] and r.json()["next_index"] == 1
    st = status(site, up.uid)
    assert st["received"] == 1 and st["bytes"] == len(up.pieces[0])
    if site.mode == "dropbox":                                     # nothing was relayed
        [session] = site.fake.sessions.values()
        assert len(session["data"]) == len(up.pieces[0])
    elif site.mode == "server":
        assert os.path.getsize(uploads.ServerUploads(site.db).part_path(up.uid)) == len(up.pieces[0])
    for i in range(1, len(up.pieces)):
        assert up.piece(i).status_code == 200
    assert up.finish().status_code == 200
    assert site.intake()[f"{up.uid}__{NAME}"] == up.raw


@pytest.mark.parametrize("gz", [True, False])
def test_a_wrong_final_checksum_is_refused_and_nothing_is_kept(site, gz):
    raw = text_report()
    up = Up(site, raw, gz=gz, sha=False)               # the start carried no checksum
    up.start()
    for i in range(len(up.pieces)):
        assert up.piece(i).status_code == 200
    wrong = hashlib.sha256(raw + b"x").hexdigest()
    r = up.finish(sha=wrong)
    assert r.status_code == 422 and "did not arrive whole, so it was not kept" in r.json()["detail"]
    assert site.intake() == {}                         # neither the file nor a sidecar
    assert status(site, up.uid)["state"] == "refused"
    again = up.finish(sha=wrong)                        # sent twice, it answers the same
    assert again.status_code == 422 and again.json()["detail"] == r.json()["detail"]
    assert up.finish().status_code == 422               # and the right checksum cannot revive it: the file is gone
    assert site.intake() == {}
    # a checksum other than the one the upload started with: the file changed while it was sent
    up2 = Up(site, raw, gz=gz)
    up2.start(new=True)
    for i in range(len(up2.pieces)):
        up2.piece(i)
    r = up2.finish(sha=wrong)
    assert r.status_code == 409 and "changed while it was sent" in r.json()["detail"]
    # damaged compressed bytes: refused in plain words
    if gz:
        junk = Up(site, raw, gz=True, sha=False)
        junk.body = b"\x1f\x8b\x08\x00" + os.urandom(3000)
        junk.pieces = [junk.body]
        junk.start()
        assert junk.piece(0).status_code == 200
        r = junk.finish()
        assert r.status_code == 422 and "compressed file" in r.json()["detail"]
    assert {k for k in site.intake() if not k.startswith(up2.uid)} == set()


def test_a_piece_too_large_and_a_file_over_the_cap_are_refused(build, monkeypatch):
    for mode in MODES:
        site = build(mode, chunk=uploads.CHUNK_BYTES if mode == "server" else 4 * 1024 * 1024)
        CH = uploads.CHUNK_BYTES
        assert CH == 4 * 1024 * 1024
        up = Up(site, os.urandom(CH + 10), gz=False)
        up.start()
        r = up.piece(0, data=os.urandom(CH + 1))
        assert r.status_code == 413 and "at most 4 MB" in r.json()["detail"]
        assert status(site, up.uid)["received"] == 0
        # REMITWATCH_UPLOAD_MAX_GB: the most a file may weigh as it is sent
        monkeypatch.setenv("REMITWATCH_UPLOAD_MAX_GB", "0.001")         # 1 MiB
        r = site.c.post("/api/uploads/start", json={"name": "big.csv", "size": 2 * 1024 * 1024, "gz": False}, headers=XHR)
        assert r.status_code == 413 and "at most 1 MB as it is sent" in r.json()["detail"]
        monkeypatch.delenv("REMITWATCH_UPLOAD_MAX_GB")
        # pieces of the right size whose sum passes the cap: the piece that would pass it is refused
        monkeypatch.setattr(uploads, "CHUNK_BYTES", 256 * 1024)
        monkeypatch.setenv("REMITWATCH_UPLOAD_MAX_GB", "0.001")
        big = Up(site, os.urandom(1300 * 1024), gz=True)               # compressed, it may be larger than the cap before
        assert big.start().status_code == 200
        codes = [big.piece(i).status_code for i in range(len(big.pieces))]
        assert codes[:4] == [200, 200, 200, 200] and codes[4] == 413
        monkeypatch.delenv("REMITWATCH_UPLOAD_MAX_GB")
        # only the last piece may be shorter, and nothing follows it
        short = Up(site, os.urandom(700 * 1024), gz=False)
        short.start()
        assert short.piece(0).status_code == 200
        r = short.piece(1, data=short.pieces[1][:1000], total=3)
        assert r.status_code == 400 and "Only the last piece" in r.json()["detail"]
        r = short.piece(1, data=short.pieces[1][:1000], total=0)       # taken as the last
        assert r.status_code == 200 and r.json()["closed"] is True
        r = short.piece(2, data=short.pieces[2], total=0)
        assert r.status_code == 409 and "The last piece has arrived already" in r.json()["detail"]
        r = short.piece(3, total=3)
        assert r.status_code in (400, 409)
        monkeypatch.setattr(uploads, "CHUNK_BYTES", 4 * 1024 * 1024)


def test_an_upload_started_under_another_sign_in_is_refused(site):
    store = site.store()
    rec = uploads.new_record("someone-else", NAME, 100, False, "production", None)
    store.create(rec)
    uid = rec["upload_id"]
    data = b"x" * 100
    assert site.c.get(f"/api/uploads/{uid}/status").status_code == 403
    r = site.c.post("/api/uploads/chunk", params={"upload_id": uid, "index": 0, "total": 1, "sha256": hashlib.sha256(data).hexdigest()},
                    content=data, headers=XHR)
    assert r.status_code == 403 and "another sign-in" in r.json()["detail"]
    assert site.c.post(f"/api/uploads/{uid}/finish", json={"sha256": "0" * 64, "chunks": 1, "bytes": 100}, headers=XHR).status_code == 403
    assert store.load(uid)[0]["chunks"] == []                        # nothing was added
    assert not any(i["upload_id"] == uid for i in site.c.get("/api/uploads/recent").json()["items"])
    # an id nobody started, and one that is not an id at all
    assert site.c.get(f"/api/uploads/{'a' * 32}/status").status_code == 404
    assert site.c.get("/api/uploads/../../etc/status").status_code in (400, 404)
    assert site.c.get(f"/api/uploads/{'A' * 32}/status").status_code == 400


def _age(site: Site, rec: dict, hours: float) -> None:
    """Make an upload's record (and its files) look `hours` old."""
    store = site.store()
    rec = dict(rec, updated_at=(datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds"))
    _r, version = store.load(rec["upload_id"])
    store.save(rec, version)
    old = time.time() - hours * 3600
    for p in (getattr(store, "part_path", lambda u: "")(rec["upload_id"]),
              os.path.join(os.path.dirname(site.db), "incoming", "sessions", rec["upload_id"] + ".json"),
              os.path.join(getattr(site.state, "root", "/nonexistent"), "upload_sessions", rec["upload_id"] + ".json")):
        if p and os.path.exists(p):
            os.utime(p, (old, old))


def test_uploads_left_unfinished_are_removed_after_a_day(site):
    store = site.store()
    stale = Up(site, text_report(), gz=False)
    stale.start()
    assert stale.piece(0).status_code == 200
    young = Up(site, text_report(seed=2), gz=False)
    young.start()
    assert young.piece(0).status_code == 200
    kept = Up(site, text_report(seed=3), gz=True)
    assert kept.send().status_code == 200
    _age(site, store.load(stale.uid)[0], 25)
    _age(site, store.load(kept.uid)[0], 25)          # finished: its record is kept for 30 days, its pair is the cycle's
    # a finish cut off between the file and its sidecar left a file alone in the intake folder, a day ago
    half = site.intake_dir(f"{'b' * 32}__half.csv")
    os.makedirs(os.path.dirname(half), exist_ok=True)
    with open(half, "wb") as fh:
        fh.write(b"half")
    os.utime(half, (time.time() - 26 * 3600,) * 2)
    pair = f"{kept.uid}__{NAME}.gz"
    os.utime(site.intake_dir(pair), (time.time() - 26 * 3600,) * 2)
    os.utime(site.intake_dir(pair + ".json"), (time.time() - 26 * 3600,) * 2)
    api_uploads._SWEPT["at"] = 0.0                    # the next request cleans up
    items = {i["upload_id"]: i for i in site.c.get("/api/uploads/recent").json()["items"]}
    assert stale.uid not in items and young.uid in items and kept.uid in items
    assert site.c.get(f"/api/uploads/{stale.uid}/status").status_code == 404
    assert store.load(stale.uid)[0] is None and store.load(young.uid)[0] is not None
    if site.mode != "dropbox":
        assert not os.path.exists(store.part_path(stale.uid)) and os.path.exists(store.part_path(young.uid))
    assert not os.path.exists(half)
    assert set(site.intake()) == {pair, pair + ".json"}      # a complete pair is never touched
    # the abandoned upload cannot be carried on: the same file starts afresh
    r = Up(site, stale.raw, gz=False).start()
    assert r.status_code == 200 and r.json()["status"] == "started" and r.json()["upload_id"] != stale.uid


# ------------------------------------------------------------------------------------------- already, and outcomes
def test_the_same_file_again_changes_nothing(site):
    raw = text_report()
    first = Up(site, raw, gz=True)
    assert first.send().status_code == 200
    pair = f"{first.uid}__{NAME}.gz"
    # waiting for the cycle: the same bytes again are not sent
    r = Up(site, raw, gz=True).start()
    assert r.status_code == 200 and r.json()["status"] == "already"
    assert r.json()["lines"] == [f"Already have this; nothing changed. This exact file was uploaded on "
                                 f"{uploads._day(datetime.now(timezone.utc).date().isoformat())} and waits for the next cycle."]
    # sent without its checksum first (an older page): the finish finds it is the same file and keeps nothing more
    twin = Up(site, raw, gz=False, sha=False)
    assert twin.send().status_code == 200
    st = status(site, twin.uid)
    assert st["state"] == "already" and st["lines"][0].startswith("Already have this; nothing changed.")
    assert sorted(site.intake()) == [pair, pair + ".json"]
    # once the cycle has read it, the report catalog knows it
    sc = json.loads(site.intake()[pair + ".json"])
    site.done(pair, dict(sc, result={"status": "applied", "lines": ["Read."]}))
    today = datetime.now(timezone.utc).date().isoformat()

    def read(c):
        c.execute("""INSERT INTO ema_report_files (file_id, report, source_name, via, status, applied_at, received_at, rows_read,
                     rows_applied, scope, svc_lo, svc_hi, posted_lo, posted_hi, as_of)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (first.sha, "production", NAME, "upload", "current", today, today, 4001, 4000, "posted", "2026-09-01",
                   "2026-10-30", "2026-10-01", today, today))
    site.fill(read)
    r = Up(site, raw, gz=True).start()
    assert r.status_code == 200 and r.json()["status"] == "already"
    assert r.json()["lines"] == [f"Already have this; nothing changed. RemitWatch read this exact file on {uploads._day(today)}."]
    st = status(site, first.uid)
    assert st["applied"] == "current" and st["lines"][0].startswith("Read on ") and "4,001 rows, 4,000 kept." in st["lines"][0]


def test_finishing_twice_answers_the_same(site):
    up = Up(site, text_report(), gz=True)
    a = up.send()
    b = up.finish()
    assert a.status_code == b.status_code == 200
    assert b.json()["state"] == "finished" and b.json()["lines"][0] == a.json()["lines"][0]
    assert len(site.intake()) == 2
    if site.mode != "server":
        assert site.triggered == ["upload"]           # one run asked for, not two


def test_what_the_cycle_made_of_a_report_is_said_from_the_catalog(tmp_path):
    """The plain result per file: what it was recognised as, its dates, rows, the boxes it replaced, a hold, and the
    checks on dates and on the 30-day A/R."""
    from remitwatch import ema_reports
    c = sqlite3.connect(":memory:")
    ema_reports.ensure_schema(c)
    rows = [
        ("ar1", "ar_report", "current", "2026-11-20", None, None, None, None, None, json.dumps({"aging_window": 30}), None, None),
        ("p1", "production", "current", "2026-11-20", "posted", "2026-01-01", "2026-11-19", "2026-11-04", "2026-11-19", "{}", None, None),
        ("p0", "production", "replaced", "2026-10-20", "posted", "2026-01-01", "2026-10-19", "2026-09-10", "2026-10-19", "{}", "p1", None),
        ("y1", "production", "current", "2026-11-20", "service", "2026-03-02", "2026-11-19", "2026-03-03", "2026-11-01", "{}", None, None),
        ("h1", "production", "held", None, "posted", None, None, None, None, "{}", None,
         "It has 40% of the claims the export of Oct 19, 2026 had for these dates: was it filtered by payer, provider or location?"),
        ("r1", "ar_report", "refused", None, None, None, None, None, None, "{}", None, "It lacks the column Claim Link."),
    ]
    for fid, rep, st, as_of, scope, slo, shi, plo, phi, acct, by, why in rows:
        c.execute("""INSERT INTO ema_report_files (file_id, report, status, as_of, scope, svc_lo, svc_hi, posted_lo, posted_hi, account,
                     replaced_by, status_reason, applied_at, received_at, rows_read, rows_applied)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (fid, rep, st, as_of, scope, slo, shi, plo, phi, acct, by, why, "2026-11-20", "2026-11-20", 100, 90))
    ar = uploads.report_outcome(c, "ar1")
    assert ar["lines"][0].startswith("Read on Nov 20, 2026 as the A/R report as of Nov 20, 2026: 100 rows, 90 kept.")
    assert [k["code"] for k in ar["checks"]] == ["ar_30_days"] and "every aging bucket" in ar["checks"][0]["message"]
    p = uploads.report_outcome(c, "p1")
    assert p["lines"][1] == "It replaces the export of postings from Sep 10, 2026 to Oct 19, 2026 (as of Oct 20, 2026)."
    assert [k["code"] for k in p["checks"]] == ["short_range"] and "16 days of postings" in p["checks"][0]["message"]
    y = uploads.report_outcome(c, "y1")
    assert [k["code"] for k in y["checks"]] == ["starts_late", "ends_early"]
    h = uploads.report_outcome(c, "h1")
    assert h["status"] == "held" and "was it filtered by payer, provider or location?" in h["lines"][0] and "Apply or Discard" in h["lines"][0]
    assert uploads.report_outcome(c, "r1")["lines"] == ["Recognised as the A/R report, and not read: It lacks the column Claim Link."]
    assert uploads.report_outcome(c, "nothing") is None
    # a file the catalog does not hold (an export, a refusal before the catalog): the result the cycle wrote is shown
    rec = dict(uploads.new_record("anonymous", "week.zip", 10, False, None, "c" * 64), state="finished", finished_at="2026-11-20")
    view = uploads.record_view(rec, conn=c, pair=("done", {"result": {"status": "not_read", "lines": ["It is not a file RemitWatch reads."]}}))
    assert view["lines"] == ["It is not a file RemitWatch reads."] and view["applied"] == "not_read"


# ----------------------------------------------------------------------------------- the five exports, the boxes
def test_the_checklist_says_what_each_export_is_and_whether_it_is_overdue(site):
    from remitwatch import ema_reports
    today = datetime.now(timezone.utc).date()
    ago = lambda n: (today - timedelta(days=n)).isoformat()      # noqa: E731

    def fill(c):
        for fid, rep, as_of, st in (("a1", "ar_report", ago(3), "current"), ("p1", "production", ago(20), "current"),
                                    ("p2", "production", ago(1), "refused")):
            c.execute("INSERT INTO ema_report_files (file_id, report, status, as_of, received_at, via) VALUES (?,?,?,?,?,?)",
                      (fid, rep, st, as_of, ago(int(as_of < ago(4)) * 15 + 2), "upload"))
        c.execute("CREATE TABLE IF NOT EXISTS files (sha256 TEXT, kind TEXT, ingested_at TEXT)")
        c.execute("INSERT INTO files (sha256, kind, ingested_at) VALUES ('s1', 'ema:charge_lines', ?)", (ago(2),))
        c.execute("INSERT INTO files (sha256, kind, ingested_at) VALUES ('s2', 'ema:era_lines', ?)", (ago(9),))
    site.fill(fill)
    j = site.c.get("/api/ema/exports").json()
    rows = {e["key"]: e for e in j["exports"]}
    assert [e["title"] for e in j["exports"]] == ["A/R report", "Production (collections) report", "Encounter & Charge Lines",
                                                  "ERA & Payment Line Adjustments", "Claim & Claim Status"]
    assert [rows[k]["overdue_days"] for k in uploads.EXPORT_KEYS] == [8, 14, 8, 8, 8]
    assert (rows["ar"]["overdue"], rows["production"]["overdue"], rows["charge_lines"]["overdue"], rows["era_lines"]["overdue"],
            rows["claim_status"]["overdue"]) == (False, True, False, True, True)
    assert rows["ar"]["age_days"] == 3 and rows["ar"]["state_line"] == "Up to date: the newest is 3 days old."
    assert rows["production"]["state_line"].startswith("Overdue: the newest is 20 days old")
    assert rows["claim_status"]["last_line"] == "Has not arrived yet." and "has none yet" in rows["claim_status"]["state_line"]
    assert "(uploaded here)" in rows["ar"]["last_line"]
    # the columns each must have are the readers' own
    assert [c["key"] for c in rows["ar"]["columns"]] == list(ema_reports.LAYOUTS["ar_report"]["required"])
    assert [c["column"] for c in rows["production"]["columns"]][:4] == ["Posted Date", "Service Date", "CPT/Product", "Check Number"]
    assert [c["key"] for c in rows["claim_status"]["columns"]] == [k for k, _l, _w in uploads.WANT["claim_status"]]
    assert all(c["why"] for e in j["exports"] for c in e["columns"])
    assert rows["production"]["columns_note"] == "All 39 columns." and rows["ar"]["columns_note"] == "All 22 columns."
    assert "Posted Date: the last five weeks, through yesterday." in rows["production"]["filters"]
    # EMA's menus are never described: the practice writes its own steps in the box
    words = json.dumps(j["exports"])
    assert not re.search(r"(?i)\bmenu\b|\bclick\b| > |→", words)
    assert all(e["ask"] and "Ask the person who last pulled it" in e["ask"] for e in j["exports"])
    assert "it last arrived" in rows["ar"]["ask"] and "it last arrived" not in rows["claim_status"]["ask"]
    # what the page needs to recognise each one: the readers' own names
    rec = j["recognition"]
    assert rec["reports"]["production"]["core"] == list(ema_reports.PRODUCTION_CORE) and rec["header_scan"] == ema_reports.HEADER_SCAN
    assert j["chunk_bytes"] == uploads.CHUNK_BYTES and j["notes_here"] is (site.mode == "server")


def test_the_how_to_box_keeps_who_saved_it_when_and_the_versions_before(site, monkeypatch):
    put = lambda body, key="ar": site.c.put(f"/api/ema/exports/{key}/howto", json=body, headers=XHR)       # noqa: E731
    box = lambda key="ar": next(e for e in site.c.get("/api/ema/exports").json()["exports"] if e["key"] == key)  # noqa: E731
    assert box()["howto"]["text"] == "" and box()["ask"]
    r = put({"text": "Step one.\r\nStep two.\x07", "name": "  Front\tdesk  ", "base_at": ""})
    assert r.status_code == 200, r.text
    first = box()
    assert first["howto"]["text"] == "Step one.\nStep two." and first["howto"]["name"] == "Front desk"
    assert first["howto"]["by"] == "anonymous" and first["howto"]["at"] and first["howto"]["versions"] == 0 and first["ask"] is None
    time.sleep(1.1)                                                 # a second later: a new saved time
    r = put({"text": "Step one, then two.", "base_at": first["howto"]["at"]})
    assert r.status_code == 200
    second = box()
    assert second["howto"]["versions"] == 1 and second["howto"]["name"] is None
    hist = site.c.get("/api/ema/exports/ar/howto").json()
    assert hist["text"] == "Step one, then two." and [v["text"] for v in hist["history"]] == ["Step one.\nStep two."]
    assert hist["history"][0]["name"] == "Front desk" and hist["history"][0]["at"] == first["howto"]["at"]
    # someone saved it after this person opened it: nothing is saved, and the answer shows their text
    r = put({"text": "Mine.", "base_at": first["howto"]["at"]})
    assert r.status_code == 409 and r.json()["howto"]["text"] == "Step one, then two."
    assert box()["howto"]["text"] == "Step one, then two."
    # limits, unknown boxes, and the other boxes untouched
    assert put({"text": "x" * (uploads.HOWTO_MAX + 1)}).status_code == 413
    assert put({"text": "x"}, key="nope").status_code == 404
    assert box("production")["howto"]["text"] == ""
    monkeypatch.setattr(uploads, "HOWTO_HISTORY", 3)
    for i in range(5):
        assert put({"text": f"version {i}"}).status_code == 200
    assert box()["howto"]["versions"] == 3
    # where it is kept: the store's meta on a server, the state folder on a serverless host
    if site.mode == "server":
        c = sqlite3.connect(site.db)
        doc = json.loads(c.execute("SELECT value FROM meta WHERE key=?", (uploads.HOWTO_META,)).fetchone()[0])
        c.close()
    else:
        doc = json.loads(site.state.get(uploads.HOWTO_STATE))
    assert doc["ar"]["text"] == "version 4" and len(doc["ar"]["history"]) == 3


# ----------------------------------------------------------------------------------------------- each host's own
def test_a_report_dropped_in_one_piece_goes_to_the_intake_folder_too(build):
    """The server's one-piece route (POST /api/uploads) hands an A/R or Production report to the intake folder as a
    file sent in pieces is, never to the weekly exports' reader; a report missing a column is refused and not kept."""
    site = build("server")
    data = F.ar_xlsx([F.ar_row() for _ in range(3)], title="A/R report")
    sha = hashlib.sha256(data).hexdigest()
    r = site.c.post("/api/uploads", params={"name": "AR 2026.xlsx"}, content=data, headers=XHR)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["status"] == "ready" and j["kind"] == "ema_report" and j["report"] == "ar_report"
    assert j["lines"][0].startswith("EMA's A/R report (Excel)") and "read by the next cycle" in j["lines"][1]
    [pair] = [k for k in site.intake() if not k.endswith(".json")]
    assert pair.endswith("__AR 2026.xlsx") and site.intake()[pair] == data
    sc = json.loads(site.intake()[pair + ".json"])
    assert {k: v for k, v in sc.items() if k != "received_at"} == {"name": "AR 2026.xlsx", "origin": "upload", "user": "anonymous",
                                                                  "sha256": sha, "size": len(data), "gz": False, "report_hint": "ar_report"}
    assert site.kicked == [site.db]
    c = sqlite3.connect(site.db)
    assert c.execute("SELECT kind, status FROM uploads WHERE upload_id=?", (sha,)).fetchone() == ("ema_report", "ready")
    c.close()
    again = site.c.post("/api/uploads", params={"name": "AR 2026.xlsx"}, content=data, headers=XHR).json()
    assert again["status"] == "already" and len(site.intake()) == 2
    cols = [x for x in F.PRODUCTION_COLUMNS if x != "Check Number"]
    bad = F.production_csv([F.production_row()], columns=cols)
    r = site.c.post("/api/uploads", params={"name": "prod.csv"}, content=bad, headers=XHR).json()
    assert r["status"] == "rejected" and "lacks 1 column RemitWatch needs: Check Number (ties each payment to its remittance)" in r["reason"]
    assert len(site.intake()) == 2 and site.kicked == [site.db]
    # once the cycle has read it, the history says what it held, from the catalog
    site.fill(lambda c: c.execute("INSERT INTO ema_report_files (file_id, report, status, applied_at, as_of, rows_read, rows_applied, account) "
                                  "VALUES (?,?,?,?,?,?,?,?)", (sha, "ar_report", "current", "2026-11-20", "2026-11-20", 3, 3,
                                                               json.dumps({"aging_window": 30}))))
    hist = {i["upload_id"]: i for i in site.c.get("/api/uploads").json()["items"]}
    assert hist[sha]["lines"][0].startswith("Read on Nov 20, 2026 as the A/R report") and "every aging bucket" in hist[sha]["lines"][1]


def test_the_cycle_is_asked_to_read_the_file_when_intake_can_be_asked(build, monkeypatch):
    site = build("server")
    calls = []
    fake = types.ModuleType("remitwatch.intake")
    fake.kick = lambda data_dir: calls.append(data_dir)
    monkeypatch.setitem(sys.modules, "remitwatch.intake", fake)
    import remitwatch
    monkeypatch.setattr(remitwatch, "intake", fake, raising=False)
    monkeypatch.setattr(uploads, "kick_intake", _real_kick)
    r = Up(site, text_report()).send()
    assert r.status_code == 200 and calls == [os.path.dirname(site.db)]
    assert "The cycle was asked to read it now." in r.json()["lines"]
    monkeypatch.setitem(sys.modules, "remitwatch.intake", None)          # a build with no intake yet
    r = Up(site, text_report(seed=9)).send()
    assert r.status_code == 200 and "The next cycle reads it." in r.json()["lines"] and len(calls) == 1


_real_kick = uploads.kick_intake


def test_a_serverless_site_asks_for_a_run_and_a_preview_takes_nothing(build, monkeypatch):
    for mode in ("local", "dropbox"):
        site = build(mode)
        r = Up(site, text_report()).send()
        assert r.status_code == 200 and site.triggered == ["upload"]
        monkeypatch.setenv("VERCEL_ENV", "preview")
        r = Up(site, text_report(seed=5)).start()
        assert r.status_code == 409 and "preview deployment" in r.json()["detail"]
        monkeypatch.delenv("VERCEL_ENV")


def test_no_request_body_over_4_mb_reaches_dropbox(build):
    site = build("dropbox", chunk=4 * 1024 * 1024)
    raw = os.urandom(9 * 1024 * 1024 + 123)
    up = Up(site, raw, gz=False)
    assert [len(p) for p in up.pieces] == [4 * 1024 * 1024, 4 * 1024 * 1024, 1024 * 1024 + 123]
    assert up.send().status_code == 200
    assert max(site.fake.bodies) == 4 * 1024 * 1024 < 4_500_000
    assert site.fake.calls["files/upload_session/start"] == 1 and site.fake.calls["files/upload_session/append_v2"] == 2
    assert site.fake.calls["files/upload_session/finish"] == 1
    assert site.intake()[f"{up.uid}__{NAME}"] == raw


def test_the_dropbox_relay_takes_a_piece_whose_answer_was_lost(build):
    site = build("dropbox", chunk=1024)
    store = site.store()
    rec = uploads.new_record("anonymous", NAME, 3000, False, None, None)
    store.create(rec)
    a, b = os.urandom(1024), os.urandom(1024)
    store.append(rec, a)
    uploads.record_piece(rec, 0, 0, hashlib.sha256(a).hexdigest(), 1024)
    store.append(rec, b)                                # reached Dropbox, then the request died before its record was saved
    store.append(rec, b)                                # sent again: Dropbox says it has it; taken as done
    with pytest.raises(uploads.PieceRefused) as exc:
        store.append(rec, os.urandom(10))               # other bytes at a place Dropbox has moved past: out of step
    assert exc.value.status == 409
    uploads.record_piece(rec, 1, 0, hashlib.sha256(b).hexdigest(), 1024)
    store.commit(rec)
    store.commit(rec)                                   # a finish whose answer was lost, sent again
    assert site.intake()[rec["pair"]] == a + b
    gone = dict(rec, session="fake-session-none", bytes=2048)
    with pytest.raises(uploads.PieceRefused) as exc:
        store.append(gone, b)
    assert exc.value.status == 410


def test_a_full_disk_is_said_plainly(build, monkeypatch):
    site = build("server")
    monkeypatch.setattr(api_uploads.shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(10 ** 12, 10 ** 12 - 1000, 1000))
    r = Up(site, text_report()).start()
    assert r.status_code == 507 and "too little room" in r.json()["detail"]


def test_storage_errors_never_say_more_than_their_kind(build, monkeypatch):
    """A Dropbox error's own text can carry a path, so a file's name: the answer says only the error's kind."""
    site = build("dropbox")
    up = Up(site, text_report())
    up.start()
    site.fake.broken["files/upload_session/start"] = RuntimeError(f"download /RemitWatch-state/intake/{NAME} failed 500")
    r = up.piece(0)
    assert r.status_code == 502 and NAME not in r.text and "RuntimeError" in r.json()["detail"]


# ------------------------------------------------------------------------------------------------ the page itself
def _node(script: str, *args: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    out = subprocess.run([node, "-e", script, UPLOAD_JS, *args], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_the_pages_sha256_is_the_real_one(tmp_path):
    rnd = random.Random(7)
    sizes = [0, 1, 55, 56, 63, 64, 65, 119, 120, 127, 128, 1000, 4096, 70000] + [rnd.randrange(1, 200000) for _ in range(12)]
    blobs = {}
    for i, n in enumerate(sizes):
        p = tmp_path / f"b{i}"
        p.write_bytes(rnd.randbytes(n))
        blobs[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
    got = _node("""
const U = require(process.argv[1]); const fs = require('fs');
const out = {};
for (const p of process.argv.slice(2)) {
  const b = new Uint8Array(fs.readFileSync(p)); const h = new U.Sha256(); let i = 0, s = 1;
  while (i < b.length) { h.update(b.subarray(i, i + s)); i += s; s = (s * 7 + 3) % 9973 + 1; }
  out[p] = h.hex();
}
console.log(JSON.stringify(out));""", *blobs)
    assert got == blobs


def test_the_page_cuts_files_into_pieces_of_exactly_4_mb_and_compresses_them(tmp_path):
    raw = text_report(150_000)                          # about 5.7 MB
    p = tmp_path / "r.csv"
    p.write_bytes(raw)
    got = _node("""
const U = require(process.argv[1]); const fs = require('fs');
(async () => {
  const file = new File([fs.readFileSync(process.argv[2])], 'r.csv');
  const res = {};
  for (const gz of [false, true]) {
    let read = 0; const sizes = []; const lasts = []; const parts = [];
    for await (const p of U.cutPieces(U.sourceStream(file, gz, 0, n => { read += n; }))) { sizes.push(p.data.length); lasts.push(p.last); parts.push(Buffer.from(p.data)); }
    fs.writeFileSync(process.argv[2] + (gz ? '.gz' : '.plain'), Buffer.concat(parts));
    res[gz ? 'gz' : 'plain'] = {sizes, lasts, read};
  }
  const from = 4 * 1024 * 1024; const parts = [];
  for await (const p of U.cutPieces(U.sourceStream(file, false, from, () => {}))) parts.push(Buffer.from(p.data));
  res.from = Buffer.concat(parts).length;
  console.log(JSON.stringify(res));
})();""", str(p))
    CH = 4 * 1024 * 1024
    assert got["plain"]["sizes"] == [CH, len(raw) - CH] and got["plain"]["lasts"] == [False, True] and got["plain"]["read"] == len(raw)
    assert (tmp_path / "r.csv.plain").read_bytes() == raw
    assert all(s == CH for s in got["gz"]["sizes"][:-1]) and got["gz"]["lasts"][-1] is True and not any(got["gz"]["lasts"][:-1])
    assert gzip.decompress((tmp_path / "r.csv.gz").read_bytes()) == raw         # one gzip member the server unpacks
    d = zlib.decompressobj(31)
    assert d.decompress((tmp_path / "r.csv.gz").read_bytes()) == raw and d.eof and not d.unused_data
    assert got["from"] == len(raw) - CH                                        # a plain file carries on where the server stopped


def _parity_files(folder) -> dict:
    """Invented files of every kind the page tells apart, with what the server's readers say of each."""
    import openpyxl

    from remitwatch import ema_exports, ema_reports
    rows = [F.production_row() for _ in range(3)]
    files = {
        "ar.xlsx": F.ar_xlsx([F.ar_row() for _ in range(3)], title="A/R report"),
        "ar.csv": F.ar_csv([F.ar_row()]),
        "prod.csv": F.production_csv(rows, title="Production report"),
        "prod_semicolon.csv": F.production_csv(rows, bom=True, delimiter=";"),
        "prod_tabs.txt": F.production_csv(rows, delimiter="\t"),
        "prod_missing.csv": F.production_csv(rows, columns=[c for c in F.PRODUCTION_COLUMNS if c not in ("Check Number", "Patient DOB")]),
        "prod_twice.csv": F.production_csv(rows, columns=F.PRODUCTION_COLUMNS + ["Payer"]),
        "prod.xlsx": F.production_xlsx(rows),
        "charge.xlsx": F.weekly_xlsx("charge_lines", []),
        "era.xlsx": F.weekly_xlsx("era_lines", []),
        "status.xlsx": F.weekly_xlsx("claim_status", []),
        "other.csv": b"Foo,Bar,Baz,Qux\r\n1,2,3,4\r\n",
    }
    wb = openpyxl.Workbook()
    wb.active.append([c for c in F.WEEKLY_COLUMNS["claim_status"] if c != "Patient ID"])
    b = io.BytesIO()
    wb.save(b)
    files["status_no_patient.xlsx"] = b.getvalue()
    out = {}
    for name, data in files.items():
        p = os.path.join(folder, name)
        with open(p, "wb") as fh:
            fh.write(data)
        try:
            rec = ema_reports.recognize(p, name)
        except ema_reports.ReportRefused as exc:
            out[p] = {"hint": exc.report, "missing": None, "twice": True}
            continue
        if rec is not None:
            out[p] = {"hint": rec.report, "missing": list(rec.missing), "twice": False}
            continue
        detail = ema_exports.read_export_detail(p)
        fam = detail["family"] if detail["family"] != "unknown" else None
        have = set(detail["canon"] or [])
        out[p] = {"hint": fam, "missing": [k for k, _l, _w in uploads.WANT[fam] if k not in have] if fam else [], "twice": False}
    return out


def test_the_page_recognises_each_export_as_the_server_does(tmp_path):
    """The page's look at the row of column names (upload.js lookAtFile) says what the server's readers say: which
    export, which required columns are missing, and a column written twice (refused)."""
    want = _parity_files(str(tmp_path))
    (tmp_path / "recognition.json").write_text(json.dumps(uploads.recognition()))
    got = _node("""
const U = require(process.argv[1]); const fs = require('fs'); const path = require('path');
(async () => {
  const R = JSON.parse(fs.readFileSync(process.argv[2]));
  const out = {};
  for (const p of process.argv.slice(3)) {
    const v = await U.lookAtFile(R, new File([fs.readFileSync(p)], path.basename(p)));
    out[p] = {hint: v.hint || null, missing: v.twice ? null : (v.missing || []), twice: !!v.twice};
  }
  console.log(JSON.stringify(out));
})();""", str(tmp_path / "recognition.json"), *want)
    assert got == want
    names = {os.path.basename(k): v for k, v in got.items()}
    assert names["prod_missing.csv"]["missing"] == ["check number", "patient dob"] and names["prod_twice.csv"]["twice"]
    assert names["status_no_patient.xlsx"] == {"hint": "claim_status", "missing": ["patient_id"], "twice": False}
    assert names["other.csv"]["hint"] is None and names["ar.xlsx"]["hint"] == "ar_report"


def test_the_upload_page_serves_its_script_and_says_what_happens_to_the_files(build):
    site = build("server")
    page = site.c.get("/upload").text
    js = site.c.get("/static/upload.js").text
    assert '<script src="/static/upload.js"></script>' in page and "EMA exports RemitWatch needs" in page
    assert "/api/uploads/chunk" in js and "CompressionStream('gzip')" in js and "/api/ema/exports" in js
    assert "Never" not in page or "never guesses EMA's menus" in page
    words = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page))
    assert "a file that does not arrive whole is not kept" in words
    assert "RemitWatch knows a file by its checksum, the same way whether it was uploaded or found in the Dropbox folder" in words
    assert "—" not in page and "—" not in js                     # no em dashes in what people read
