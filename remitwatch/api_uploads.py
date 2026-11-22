"""
Large files on the Upload page, and its checklist of the five EMA exports. remitwatch.uploads does the work; this is the
part a browser talks to.

    POST /api/uploads/start                                {name, size, gz, report_hint, sha256}: an upload id, the upload
                                                           of this file that was cut off (to carry on), or "already have this"
    POST /api/uploads/chunk?upload_id&index&total&sha256   one piece of the file as the request's body (at most 4 MiB)
    GET  /api/uploads/{upload_id}/status                   what has arrived (to carry on after a break), and what became of it
    POST /api/uploads/{upload_id}/finish                   {sha256, chunks, bytes}: the whole file checked, then handed to
                                                           the intake folder the next cycle reads
    GET  /api/uploads/recent                               the signed-in person's uploads of the last 30 days
    GET  /api/ema/exports                                  the five exports: what each is for, its filters and columns, when
                                                           it last arrived and whether it is overdue, the practice's own steps
    GET  /api/ema/exports/{key}/howto                      one "How we pull this in our EMA" box, with its earlier versions
    PUT  /api/ema/exports/{key}/howto                      {text, name, base_at}: saves the box (any signed-in person)

Every route needs the sign-in, as every other route does (api.require_auth); the ones that change something also need the
header a cross-site form cannot set (api.require_xhr). Both hosts take these uploads: a server keeps the pieces beside the
store, a serverless host relays each piece to a Dropbox upload session, so no request body is ever over 4 MiB. Nothing
here writes a file's name, or what is in a file, to a log: an error says only its kind.
"""
from __future__ import annotations

import asyncio
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from . import runtime, uploads

router = APIRouter()
_security = HTTPBasic(auto_error=False)
_SWEPT = {"at": 0.0}
DISK_MARGIN = 512 * 1024 * 1024         # a server keeps this much disk free besides the file it takes
RECENT_MAX = 30                          # uploads listed by /api/uploads/recent


def _api():
    from . import api
    return api


def _user(request: Request, credentials: Optional[HTTPBasicCredentials] = Depends(_security)) -> str:
    """The same sign-in as every other route."""
    return _api().require_auth(request, credentials)


def _xhr(request: Request) -> None:
    """The same guard against cross-site forms as every other route that changes something."""
    _api().require_xhr(request)


def _answer(exc: uploads.PieceRefused) -> JSONResponse:
    return JSONResponse({"detail": exc.message, "next_index": exc.next_index}, status_code=exc.status,
                        headers={"Cache-Control": "no-store"})


def _store():
    """This host's uploads: beside the store on a server; in the state folder on a serverless host."""
    if runtime.serverless():
        from . import cloud
        st = cloud.SERVING_COPY.state()
        if st is None:
            raise HTTPException(503, "This site's storage is not set up yet: open /setup.")
        return uploads.state_uploads(st)
    return uploads.ServerUploads(_api().DB_PATH)


@contextmanager
def _conn() -> Iterator[Any]:
    """The store (a server) or the dashboard's copy of it (a serverless host), to look up what RemitWatch has already
    read; None when neither can be opened just now."""
    c = None
    try:
        c = _api().db()
    except Exception:
        c = None
    try:
        yield c
    finally:
        if c is not None:
            c.close()


@contextmanager
def _storage() -> Iterator[None]:
    """A storage error is answered in plain words with its kind only: its own text can name a file."""
    try:
        yield
    except (HTTPException, uploads.PieceRefused):
        raise
    except Exception as exc:
        raise HTTPException(502, f"The upload store did not answer as expected ({type(exc).__name__}). Try again in a minute; "
                                 "an upload carries on from the last piece that arrived.") from None


def _sweep(store) -> None:
    now = time.time()
    if now - _SWEPT["at"] < uploads.SWEEP_EVERY:
        return
    _SWEPT["at"] = now
    try:
        store.sweep(now)
    except Exception:
        pass                    # tried again by the next request after SWEEP_EVERY


def _own(rec: dict | None, user: str) -> dict:
    if rec is None:
        raise HTTPException(404, "There is no upload with that id: it was left unfinished for more than a day and removed, "
                                 "or it finished long ago. Choose the file again to send it.")
    if rec.get("user") != user:
        raise HTTPException(403, "This upload was started under another sign-in.")
    return rec


def _check_id(upload_id: str) -> str:
    if not uploads.UPLOAD_ID.fullmatch(upload_id or ""):
        raise HTTPException(400, "The upload id is not valid.")
    return upload_id


def _reveal() -> bool:
    return bool(_api().SHOW_PHI)


# ----------------------------------------------------------------------------------------------------------- starting
@router.post("/api/uploads/start", dependencies=[Depends(_xhr)])
async def upload_start(body: dict = Body(default={}), user: str = Depends(_user)):
    """Start sending a file in pieces. The page gives the file's name, its size, whether it compresses it on the way, what
    it recognised it as from its column names, and the SHA-256 of the file as it is: when RemitWatch already has a file
    of exactly these bytes, nothing is sent ("already"); when this person's upload of it was cut off, it carries on
    from the first piece that did not arrive ("resume")."""
    if runtime.serverless() and runtime.preview():
        raise HTTPException(409, "This is a preview deployment. Only the production site takes the practice's files.")
    try:
        name, size, gz, hint, sha = uploads.check_start(body.get("name"), body.get("size"), body.get("gz"),
                                                        body.get("report_hint"), body.get("sha256"))
    except uploads.PieceRefused as exc:
        return _answer(exc)
    store = _store()
    return await asyncio.to_thread(_start, store, user, name, size, gz, hint, sha, bool(body.get("new")))


def _start(store, user: str, name: str, size: int, gz: bool, hint: str | None, sha: str | None, new: bool):
    with _storage():
        _sweep(store)
        if not store.serverless:
            root = uploads.data_dir(store.db_path)
            need = min(size, uploads.max_upload_bytes()) + DISK_MARGIN
            if shutil.disk_usage(root).free < need:
                return _answer(uploads.PieceRefused(507, "The server's disk has too little room for this file just now. "
                                                         "Ask whoever looks after the server to free some space."))
        if sha:
            with _conn() as c:
                seen = uploads.known_file(c, sha, store.pending())
            if seen:
                return {"status": "already", "lines": [seen["line"]], "already": {k: seen.get(k) for k in ("how", "status", "at")}}
            if not new:
                for rec in store.records(recent=True):
                    if (rec.get("user") == user and rec.get("state") == "receiving" and rec.get("sha256") == sha
                            and rec.get("size") == size and bool(rec.get("gz")) == gz):
                        view = uploads.record_view(rec, reveal=_reveal(), pieces=True)
                        return dict(view, status="resume", next_index=view["received"])
        rec = uploads.new_record(user, name, size, gz, hint, sha)
        store.create(rec)
        return dict(uploads.record_view(rec, reveal=_reveal(), pieces=True), status="started", next_index=0)


# ----------------------------------------------------------------------------------------------------------- pieces
@router.post("/api/uploads/chunk", dependencies=[Depends(_xhr)])
async def upload_chunk(request: Request, upload_id: str = Query(..., max_length=32), index: int = Query(..., ge=0, le=1_000_000),
                       total: int = Query(0, ge=0, le=1_000_000), sha256: str = Query(..., max_length=64),
                       user: str = Depends(_user)):
    """One piece, its bytes as the request's body, with its place (index, from 0), the number of pieces when the page
    knows it (0 until the last piece of a compressed file), and its SHA-256. A piece sent again after its answer was
    lost changes nothing; a piece out of order, too large, or whose checksum does not match is refused, and the answer
    says which piece the upload waits for."""
    _check_id(upload_id)
    if not uploads.SHA256_HEX.fullmatch(sha256 or ""):
        raise HTTPException(400, "The piece's checksum is not valid.")
    data = bytearray()
    async for part in request.stream():
        data += part
        if len(data) > uploads.CHUNK_BYTES:
            return _answer(uploads.PieceRefused(413, f"A piece can be at most {uploads.CHUNK_BYTES // (1024 * 1024)} MB. "
                                                     "The page cuts files into pieces of that size."))
    store = _store()
    return await asyncio.to_thread(_chunk, store, user, upload_id, index, total, sha256, bytes(data))


def _chunk(store, user: str, uid: str, index: int, total: int, sha: str, data: bytes):
    with _storage(), store.lock(uid):
        rec, version = store.load(uid)
        _own(rec, user)
        try:
            if not uploads.check_piece(rec, index, total, sha, data):
                return {"received": len(rec["chunks"]), "bytes": rec["bytes"], "again": True}
            store.append(rec, data)
        except uploads.PieceRefused as exc:
            return _answer(exc)
        uploads.record_piece(rec, index, total, sha, len(data))
        try:
            store.save(rec, version)
        except uploads.Stale:
            again, _v = store.load(uid)
            got = (again or {}).get("chunks") or []
            if len(got) > index and got[index] == sha:
                return {"received": len(got), "bytes": again["bytes"], "again": True}
            return _answer(uploads.PieceRefused(409, "Two requests for this upload crossed. The page carries on from the last "
                                                     "piece that arrived.", len(got)))
        return {"received": len(rec["chunks"]), "bytes": rec["bytes"], "closed": bool(rec.get("closed"))}


@router.get("/api/uploads/recent")
def uploads_recent(user: str = Depends(_user)):
    """The signed-in person's uploads of the last 30 days, newest first: those cut off (to carry on), and what became of
    the finished ones."""
    store = _store()
    with _storage():
        _sweep(store)
        recs = sorted((r for r in store.records() if r.get("user") == user), key=lambda r: r.get("created_at") or "",
                      reverse=True)[:RECENT_MAX]
        done = [r for r in recs if r.get("state") == "finished"]
        with ThreadPoolExecutor(max_workers=8) as ex:
            pairs = dict(zip([r["upload_id"] for r in done], ex.map(lambda r: store.pair_state(r["pair"]), done)))
        with _conn() as c:
            items = [uploads.record_view(r, conn=c, pair=pairs.get(r["upload_id"]), reveal=_reveal()) for r in recs]
    return {"items": items, "mode": "serverless" if runtime.serverless() else "server"}


@router.get("/api/uploads/{upload_id}/status")
def upload_status(upload_id: str, user: str = Depends(_user)):
    """Where an upload stands: the pieces received (each piece's SHA-256, for the page to carry on from the first that
    did not arrive), and once it is finished, what the cycle made of the file."""
    _check_id(upload_id)
    store = _store()
    with _storage():
        rec, _v = store.load(upload_id)
        _own(rec, user)
        pair = store.pair_state(rec["pair"]) if rec.get("state") == "finished" else None
        with _conn() as c:
            view = uploads.record_view(rec, conn=c, pair=pair, reveal=_reveal(), pieces=True)
    return dict(view, next_index=view["received"])


# ----------------------------------------------------------------------------------------------------------- finishing
@router.post("/api/uploads/{upload_id}/finish", dependencies=[Depends(_xhr)])
def upload_finish(upload_id: str, request: Request, body: dict = Body(default={}), user: str = Depends(_user)):
    """The last piece is in: the whole file is checked against the SHA-256 of the original the page gives (unpacked on
    the way when it came compressed), then it goes to the intake folder with its sidecar, for the next cycle. A file
    RemitWatch already has is not kept ("already"); one that does not match is not kept either ("refused"). Sent
    twice, it answers the same."""
    _check_id(upload_id)
    sha = str(body.get("sha256") or "").lower()
    try:
        chunks, nbytes = int(body.get("chunks", -1)), int(body.get("bytes", -1))
    except (TypeError, ValueError):
        raise HTTPException(400, "The number of pieces is missing.") from None
    store = _store()
    with _storage(), store.lock(upload_id):
        rec, version = store.load(upload_id)
        _own(rec, user)
        if rec.get("state") != "receiving":
            return _finished(store, rec, woke=None)
        try:
            uploads.check_finish(rec, sha, chunks, nbytes)
        except uploads.PieceRefused as exc:
            return _answer(exc)
        rec["sha256"] = sha
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        store.commit(rec)
        checked = True
        try:
            bad = uploads.verify(store.committed(rec), gz=bool(rec.get("gz")), sha256=sha, size=int(rec["size"]),
                                 deadline=time.time() + uploads.VERIFY_SECONDS if store.serverless else None)
        except TimeoutError:
            bad, checked = None, False          # a very large file: the cycle checks the sidecar's SHA-256 as it reads it
        if bad:
            store.discard(rec)
            rec.update(state="refused", finished_at=now, updated_at=now,
                       message=f"The file did not arrive whole, so it was not kept: {bad} Choose it again to send it again.")
            _save_end(store, rec, version)
            return _finished(store, rec, woke=None)
        with _conn() as c:
            seen = uploads.known_file(c, sha, store.pending(), skip_pair=rec["pair"])
        if seen:
            store.discard(rec)
            rec.update(state="already", finished_at=now, updated_at=now, message=seen["line"])
            _save_end(store, rec, version)
            return _finished(store, rec, woke=None)
        store.write_sidecar(rec, uploads.make_sidecar(rec["name"], rec["user"], sha, rec["size"], bool(rec.get("gz")),
                                                      rec.get("report_hint"), received_at=now))
        rec.update(state="finished", finished_at=now, updated_at=now, checked=checked)
        _save_end(store, rec, version)
    woke = _wake(store, request)
    return _finished(store, rec, woke=woke)


def _save_end(store, rec: dict, version: Any) -> None:
    try:
        store.save(rec, version)
    except uploads.Stale:
        again, v2 = store.load(rec["upload_id"])
        if again is not None and again.get("state") == "receiving":
            store.save(rec, v2)             # a piece sent again crossed the finish: the finish stands


def _wake(store, request: Request) -> bool:
    """Ask for the file to be read soon: the server's cycle reads its intake folder (remitwatch.intake.kick); a
    serverless host asks for a run the way the Sync button does."""
    if not store.serverless:
        return uploads.kick_intake(store.db_path)
    if runtime.preview():
        return False
    try:
        from . import cloud
        ok = cloud.trigger_cycle(_api().base_url(request), "upload")
        cloud.SERVING_COPY._status = (0.0, None)
        return bool(ok)
    except Exception:
        return False


def _finished(store, rec: dict, woke: bool | None):
    """The answer to a finish, the same however often it is sent: the upload as the page shows it; a file that did not
    arrive whole is a 422."""
    pair = store.pair_state(rec["pair"]) if rec.get("state") == "finished" else None
    with _conn() as c:
        view = uploads.record_view(rec, conn=c, pair=pair, reveal=_reveal())
    if rec.get("state") == "refused":
        return JSONResponse(dict(view, detail=rec.get("message") or "Not taken."), status_code=422)
    if rec.get("state") == "finished" and woke is not None and pair and pair[0] == "waiting":
        if store.serverless:
            view["lines"].append("The next run was asked to start now; it reads the file first." if woke else
                                 "The next run reads it: the daily run, or press Sync and analyse now on the Data tab.")
        else:
            view["lines"].append("The cycle was asked to read it now." if woke else "The next cycle reads it.")
        if rec.get("checked") is False:
            view["lines"].append("The file is large, so the run checks its checksum as it reads it.")
    view["woke"] = woke
    return view


# ------------------------------------------------------------------------------------------------- the five EMA exports
def _howto_doc(c) -> dict:
    """The "How we pull this in our EMA" boxes: the store's meta on a server; the state folder's file on a serverless
    host (the dashboard's copy of the store's meta when the folder has none)."""
    if runtime.serverless():
        from . import cloud
        st = cloud.SERVING_COPY.state(fast=True)
        if st is not None:
            try:
                doc = uploads.howto_read_state(st)
                if doc is not None:
                    return doc
            except Exception:
                pass
    return uploads.howto_read_conn(c)


@router.get("/api/ema/exports")
def ema_exports_list(user: str = Depends(_user)):
    """The checklist of the five EMA exports, row by row, with what the page needs to recognise each one from its
    column names before it sends anything."""
    try:
        pending = _store().pending()        # an upload waiting for the cycle makes its row "received", not overdue
    except Exception:
        pending = []
    with _conn() as c:
        rows = uploads.exports_overview(c, _howto_doc(c), pending=pending)
    serverless = runtime.serverless()
    return {"exports": rows, "recognition": uploads.recognition(), "mode": "serverless" if serverless else "server",
            "chunk_bytes": uploads.CHUNK_BYTES, "chunk_from": uploads.CHUNK_FROM_BYTES, "max_bytes": uploads.max_upload_bytes(),
            "notes_here": not serverless, "show_phi": _reveal(), "user": user}


@router.get("/api/ema/exports/{key}/howto")
def ema_howto_get(key: str, user: str = Depends(_user)):
    if key not in uploads.EXPORT_KEYS:
        raise HTTPException(404, "There is no such export.")
    with _conn() as c:
        box = _howto_doc(c).get(key) or {}
    return dict(uploads._howto_public(box), key=key, history=list(box.get("history") or []))


@router.put("/api/ema/exports/{key}/howto", dependencies=[Depends(_xhr)])
def ema_howto_put(key: str, body: dict = Body(default={}), user: str = Depends(_user)):
    """Save the practice's own steps for pulling one export from EMA. Any signed-in person may; the box keeps who saved
    each version and when, and the versions before it. `base_at` is when the version the person started from was saved
    (empty for an empty box): when someone saved the box since, nothing is saved and the answer says so."""
    if key not in uploads.EXPORT_KEYS:
        raise HTTPException(404, "There is no such export.")
    base_at = body.get("base_at")
    try:
        with _storage():
            if runtime.serverless():
                from . import cloud
                st = cloud.SERVING_COPY.state()
                if st is None:
                    raise HTTPException(503, "This site's storage is not set up yet: open /setup.")
                with _conn() as c:
                    fallback = uploads.howto_read_conn(c)
                doc = uploads.howto_save_state(st, key, body.get("text"), user, body.get("name"),
                                               None if base_at is None else str(base_at), fallback=fallback)
            else:
                doc = uploads.howto_save_server(_api().DB_PATH, key, body.get("text"), user, body.get("name"),
                                                None if base_at is None else str(base_at))
    except uploads.PieceRefused as exc:
        if exc.status == 409:
            with _conn() as c:
                cur = _howto_doc(c).get(key) or {}
            return JSONResponse({"detail": exc.message, "howto": uploads._howto_public(cur)}, status_code=409)
        return _answer(exc)
    box = doc.get(key) or {}
    return {"key": key, "howto": uploads._howto_public(box)}
