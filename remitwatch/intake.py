"""
The upload hand-off: what people upload in the app reaches the cycle through an intake folder.

A finished upload (remitwatch.api_uploads, remitwatch.uploads) becomes one file and a JSON sidecar beside it:

    server      <data_dir>/incoming/ready/<upload_id>__<safe name>[.gz]       owner-only, beside the store
    serverless  <state folder>/intake/<upload_id>__<safe name>[.gz]           REMITWATCH_STATE_PATH in Dropbox

The sidecar, <same name>.json, is {"name", "origin": "upload", "user", "sha256", "size", "gz", "received_at",
"report_hint"}. sha256 and size are those of the original bytes (before the page compressed them), so an upload and the
same file found in the Dropbox folder are one file: whichever arrives first is read, the other is "already". The sidecar
is written last; a file without one is still arriving and is left alone.

The cycle reads each complete pair, oldest first: it checks the SHA-256 of the original bytes as it reads (a .gz is
unpacked as it is read, never whole), reads the file with origin 'upload' (EMA's A/R and Production reports through
remitwatch.ema_reports.ingest_file, anything else through the router, dropbox_sync.ingest_new_files), and moves the pair
to done/ with what became of it added to the sidecar ("result": {"status", "lines", ...}), whether it was applied or
refused. The report catalog row (ema_report_files, by the file's SHA-256) keeps the origin: the upload id, who sent it,
and where the kept copy is, so a held file can be applied later and a re-read finds it.

    server      process_ready(store, ...) at the start of each cycle (pipeline.run_cycle, under its lock), and kick()
                after an upload finishes, which asks the scheduler for a cycle soon
    serverless  process_state(store, state, ...) at the start of each run (cloud.run_cloud_cycle), before the Dropbox
                delta; a Production report goes through the run's one-Production-file-per-run slot
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import time
import zlib
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

INCOMING, READY, DONE = "incoming", "ready", "done"
STATE_INTAKE = "intake"
SIDECAR_KEYS = ("name", "origin", "user", "sha256", "size", "gz", "received_at", "report_hint")
BLOCK = 4 * 1024 * 1024             # Dropbox's content hash works in blocks of 4 MiB
MAX_LINES = 12


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------------------------------------------------ hashing
class Hashes:
    """The SHA-256, the Dropbox content hash and the size of the original bytes, fed piece by piece."""

    def __init__(self) -> None:
        self.sha = hashlib.sha256()
        self.blocks: list[bytes] = []
        self._block = hashlib.sha256()
        self._in_block = 0
        self.size = 0

    def update(self, data: bytes) -> None:
        self.sha.update(data)
        self.size += len(data)
        mv = memoryview(data)
        while mv:
            take = min(len(mv), BLOCK - self._in_block)
            self._block.update(mv[:take])
            self._in_block += take
            mv = mv[take:]
            if self._in_block == BLOCK:
                self.blocks.append(self._block.digest())
                self._block, self._in_block = hashlib.sha256(), 0

    def sha256(self) -> str:
        return self.sha.hexdigest()

    def content_hash(self) -> str:
        blocks = self.blocks + ([self._block.digest()] if self._in_block or not self.blocks else [])
        return hashlib.sha256(b"".join(blocks)).hexdigest()


def hash_file(path: str, gz: bool) -> Hashes:
    """The hashes of a file's original bytes: a .gz is unpacked as it is read (never whole). Raises ValueError for a
    file that says it is gzip and is not."""
    h = Hashes()
    try:
        if gz:
            with gzip.open(path, "rb") as fh:
                for piece in iter(lambda: fh.read(1 << 20), b""):
                    h.update(piece)
        else:
            with open(path, "rb") as fh:
                for piece in iter(lambda: fh.read(1 << 20), b""):
                    h.update(piece)
    except (OSError, EOFError, zlib.error) as exc:
        raise ValueError(f"The file could not be unpacked ({type(exc).__name__}).") from exc
    return h


# ---------------------------------------------------------------------------------------------------------- sidecars
def read_sidecar(raw: bytes | None) -> dict | None:
    try:
        sc = json.loads(raw) if raw else None
    except ValueError:
        return None
    return sc if isinstance(sc, dict) and sc.get("sha256") else None


def safe_name(pair: str) -> str:
    """The file's own (made safe) name, from the pair's name: <upload_id>__<safe name>[.gz] without the upload id."""
    return pair.split("__", 1)[1] if "__" in pair else pair


def upload_id_of(pair: str) -> str:
    return pair.split("__", 1)[0] if "__" in pair else ""


def _plain_name(sc: dict, pair: str) -> str:
    """The name the file is read under: the name the person's file had (from the sidecar), less any folder, and with
    nothing a file name cannot hold; failing that, the safe name of the pair (less .gz)."""
    name = os.path.basename(str(sc.get("name") or "").replace("\\", "/")).strip()
    name = "".join(ch for ch in name if ch not in '<>:"/\\|?*' and ord(ch) >= 32).strip(" .")
    if not name:
        name = safe_name(pair)
        if sc.get("gz") and name.lower().endswith(".gz"):
            name = name[:-3]
    return name[:160] or "file"


# ------------------------------------------------------------------------------------------------------- the result
def _n(k: int, word: str) -> str:
    return f"{k:,} {word}" + ("" if k == 1 else "s")


def report_lines(res: dict) -> list[str]:
    """What the report reader made of one file, in plain words (the Upload page shows these)."""
    from . import ema_reports as er

    st = res.get("status")
    title = er.REPORT_NAMES.get(res.get("report") or "", "EMA report")
    why = (res.get("reason") or "").strip()
    if st == "current":
        lines = [f"Read as the {title}: {_n(int(res.get('rows_read') or 0), 'row')}, "
                 f"{int(res.get('rows_applied') or 0):,} applied."]
        if why:
            lines.append(why)
        return lines
    if st == "superseded":
        return [f"Read as the {title}. " + (why or "Newer exports on file cover everything it holds.")]
    if st == "already":
        return ["Already have this; nothing changed."]
    if st == "held":
        return [f"Recognised as the {title}, and held: {why} Apply or Discard it on the Data tab."]
    if st == "refused":
        return [f"Recognised as the {title}, and not read: {why}"]
    if st == "waiting":
        return [f"Recognised as the {title}, kept and not read yet: {why}".strip()]
    return [f"{title}: {st}."]


# The hand-off's words for what became of a pair (the Upload page reads them): applied, already, held, refused, waiting,
# not_read, failed. The reader's own word stays beside it as 'outcome' (a catalog status such as current or superseded,
# or read / not_read / too_large for a file that is no report).
RESULT_WORDS = {"current": "applied", "partly_replaced": "applied", "replaced": "applied", "superseded": "applied",
                "read": "applied", "already": "already", "held": "held", "refused": "refused", "discarded": "refused",
                "waiting": "waiting", "not_read": "not_read", "too_large": "not_read", "failed": "failed", "error": "failed"}


def _result(status: str, lines: Iterable[str], **kw: Any) -> dict:
    return dict(kw, status=RESULT_WORDS.get(status, status), outcome=status, lines=[str(x) for x in lines][:MAX_LINES], at=_now())


def _summarise(res: dict) -> dict:
    """One result for a pair from what the readers returned (a zip gives one per report in it)."""
    if res.get("kind") == "router":
        r = res["router"]
        reps = r.get("ema_reports") or []
        if reps:
            return _result(reps[0].get("status") or "read", res.get("lines") or [], report=reps[0].get("report"),
                           file_id=reps[0].get("file_id"))
        if r.get("failed"):
            return _result("refused", res.get("lines") or ["Not read."])
        if r.get("remits") or r.get("ema") or r.get("claims_837"):
            return _result("read", res.get("lines") or ["Read."])
        return _result("not_read", res.get("lines") or ["Not read: it is none of the files RemitWatch reads."])
    if res.get("status") == "zip":
        members = res.get("members") or []
        if not members:
            return _result("not_read", ["Not read: the zip holds no A/R or Production report."])
        lines: list[str] = []
        for m in members:
            lines += report_lines(m)
        best = next((m for m in members if m.get("status") in ("current", "superseded")), members[0])
        return _result(best.get("status") or "read", lines, report=best.get("report"), file_id=best.get("file_id"),
                       rows_applied=sum(int(m.get("rows_applied") or 0) for m in members))
    return _result(res.get("status") or "read", report_lines(res), report=res.get("report"), file_id=res.get("file_id"),
                   rows_applied=int(res.get("rows_applied") or 0))


def _router_lines(r: dict) -> list[str]:
    lines = []
    if r.get("remits"):
        lines.append(f"Read: {_n(int(r['remits']), 'remittance file')}.")
    if r.get("claims_837"):
        lines.append(f"Read: {_n(int(r['claims_837']), 'claim file')}.")
    if r.get("ema"):
        lines.append("Read as an EMA export.")
    if r.get("skipped") and not lines:
        lines.append("Nothing new: already read, or none of the files RemitWatch reads.")
    for f in r.get("failed") or []:
        lines.append(f"Not read: {f}.")
    return lines


# -------------------------------------------------------------------------------------------------------- reading
Production = Callable[..., dict]


def is_production(path: str, name: str) -> bool:
    """Whether a file is (or holds) EMA's Production report: on a serverless host such a file takes the run's one
    Production slot."""
    from . import ema_reports as er
    try:
        if er.is_plain_zip(path):
            return any(r.report == "production" for r in er.recognize_zip(path, name))
        rec = er.recognize(path, name)
    except (er.ReportRefused, OSError, ValueError):
        return False
    return bool(rec) and rec.report == "production"


def read_file(store, path: str, *, name: str, origin: dict, file_id: str, ledger=None, production: Production | None = None,
              deadline: float | None = None, work_dir: str | None = None, log=print) -> dict:
    """Read one uploaded file whose bytes are already checked. EMA's reports go to ema_reports.ingest_file (a
    Production report through `production` when given: the serverless run's slot); anything else to the router
    (dropbox_sync.ingest_new_files), under the file's own name. Returns the readers' result (with 'kind': 'router' for
    a file the router read)."""
    from . import ema_reports as er
    from .dropbox_sync import ingest_new_files

    try:
        zipped = er.is_plain_zip(path)
        rec = None if zipped else er.recognize(path, name)
        report = zipped and bool(er.recognize_zip(path, name)) or bool(rec)
    except er.ReportRefused:
        zipped, rec, report = False, "refused", True
    if report:
        if production is not None and is_production(path, name):
            return production(path, None if rec == "refused" else rec, file_id=file_id, origin=origin, name=name, log=log)
        kw: dict = {"name": name} if _takes_name() else {}
        return er.ingest_file(store, path, None if rec in (None, "refused") or zipped else rec, via="upload", origin=origin,
                              ledger=ledger, deadline=deadline, log=log, **kw)
    # not a report: the router reads it under its own name (an 835, a claim file, one of the weekly exports)
    folder = os.path.join(work_dir or os.path.dirname(path), "read-" + (origin.get("upload_id") or file_id[:16]))
    os.makedirs(folder, exist_ok=True)
    if os.path.abspath(folder) == os.path.abspath(os.path.dirname(path)):
        folder = os.path.join(folder, "read")
        os.makedirs(folder, exist_ok=True)
    gz = path.lower().endswith(".gz") or _gzip_head(path)
    target = os.path.join(folder, name[:-3] if gz and name.lower().endswith(".gz") else name)
    if gz:
        from .dropbox_sync import MAX_EXPORT_BYTES
        n = 0
        with gzip.open(path, "rb") as src, open(target, "wb") as dst:
            for piece in iter(lambda: src.read(1 << 20), b""):
                n += len(piece)
                if n > max(MAX_EXPORT_BYTES, 400 << 20):
                    break
                dst.write(piece)
    else:
        try:
            os.link(path, target)
        except OSError:
            shutil.copyfile(path, target)
    r = ingest_new_files(store, [target], log=log, origins={target: origin}, ledger=ledger, via="upload", deadline=deadline)
    r.pop("remit_stats", None)
    return {"kind": "router", "router": r, "lines": _router_lines(r), "path": target}


def _gzip_head(path: str) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(2) == b"\x1f\x8b"
    except OSError:
        return False


def _takes_name() -> bool:
    """Whether ema_reports.ingest_file takes the file's own name (it reads a working copy under another name)."""
    import inspect
    from . import ema_reports as er
    try:
        return "name" in inspect.signature(er.ingest_file).parameters
    except (TypeError, ValueError):
        return False


def _supersede_twins(store, hashes: Hashes, file_id: str) -> None:
    """A raw CSV that a serverless host recognised from its first bytes and left waiting (too large to read there) is
    the same file as this upload when its Dropbox content hash is the upload's: it is closed, read once."""
    from . import ema_reports as er
    try:
        er.supersede_waiting(store.conn, er.provisional_id(hashes.content_hash()),
                             reason="The same file was uploaded in the app and read from there.", replaced_by=file_id)
    except Exception:       # noqa: BLE001  (nothing waits under that id)
        pass


def _fill_origin(conn, file_id: str, origin: dict) -> None:
    """A catalog row with no origin (restored from the posting ledger after a run that saved the ledger and died before
    saving the store) takes the upload's: where its kept copy is, who sent it. Committed with the run's next write."""
    try:
        row = conn.execute("SELECT origin, source_kind FROM ema_report_files WHERE file_id=?", (file_id,)).fetchone()
    except Exception:       # noqa: BLE001  (no catalog: nothing to fill)
        return
    if row is None:
        return
    if not row[0]:
        conn.execute("UPDATE ema_report_files SET origin=? WHERE file_id=?", (json.dumps(origin, sort_keys=True), file_id))
    if not row[1]:
        conn.execute("UPDATE ema_report_files SET source_kind='upload' WHERE file_id=?", (file_id,))


def _mark_uploads_row(conn, sha: str, done_path: str | None) -> None:
    """An upload sent in one piece also has a row in the uploads table (status 'ready'): it is read now."""
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='uploads'").fetchone():
            return
        cols = {r[1] for r in conn.execute("PRAGMA table_info(uploads)")}
        sets = "status='done', done_at=?" + (", path=?" if done_path and "path" in cols else "")
        args: list = [_now()] + ([done_path] if done_path and "path" in cols else [])
        conn.execute(f"UPDATE uploads SET {sets} WHERE upload_id=? AND status='ready'", args + [sha])
        conn.commit()
    except Exception:       # noqa: BLE001  (the uploads table is the Upload page's own)
        pass


# ------------------------------------------------------------------------------------------------------------- server
def ready_dir(data_dir: str) -> str:
    return os.path.join(data_dir, INCOMING, READY)


def done_dir(data_dir: str) -> str:
    return os.path.join(data_dir, INCOMING, DONE)


def _private_dir(path: str) -> str:
    os.makedirs(path, mode=0o700, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def _write_private(path: str, data: bytes) -> None:
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def pending(data_dir: str) -> list[dict]:
    """The complete pairs waiting in ready/ (each sidecar with 'pair'), oldest first."""
    folder = ready_dir(data_dir)
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    out = []
    for f in names:
        if f.endswith(".json") and not f.startswith(".") and os.path.isfile(os.path.join(folder, f[:-5])):
            try:
                with open(os.path.join(folder, f), "rb") as fh:
                    sc = read_sidecar(fh.read())
            except OSError:
                sc = None
            if sc is not None:
                out.append(dict(sc, pair=f[:-5]))
    return sorted(out, key=lambda s: (str(s.get("received_at") or ""), s["pair"]))


def process_ready(store, data_dir: str | None = None, *, ledger=None, deadline: float | None = None, log=print) -> dict:
    """Read every complete pair in <data_dir>/incoming/ready/ (a server), oldest first: check its bytes against the
    sidecar, move the file to done/, read it from there (its kept copy) with origin 'upload', and write the sidecar to
    done/ with the result. `ledger` is the cycle's ledger opener (a Production report is applied with it). Returns
    counts: pairs, read, refused, changed (whether anything new reached the store), and per pair its result."""
    data_dir = data_dir or os.path.dirname(os.path.abspath(store.db_path))
    out: dict = {"pairs": 0, "read": 0, "refused": 0, "changed": False, "results": []}
    _finish_orphans(store, data_dir, ledger=ledger, log=log, out=out)
    todo = pending(data_dir)
    if not todo:
        return out
    done = _private_dir(done_dir(data_dir))
    for sc in todo:
        if deadline is not None and time.time() > deadline:
            out["remaining"] = out.get("remaining", 0) + 1
            continue
        pair = sc["pair"]
        src = os.path.join(ready_dir(data_dir), pair)
        out["pairs"] += 1
        res = _server_pair(store, src, os.path.join(done, pair), sc, ledger=ledger, deadline=deadline, log=log)
        _close_pair(data_dir, pair, sc, res)
        out["results"].append({"pair": pair, **{k: res.get(k) for k in ("status", "report", "file_id")}})
        if res["status"] in ("refused", "not_read", "failed"):
            out["refused"] += 1
        else:
            out["read"] += 1
        if res["status"] not in ("already",):
            out["changed"] = True
    return out


def _no_names(log, sc: dict, pair: str):
    """The readers' log lines with the upload's own file name replaced by its upload id: a person's file name can carry
    a patient's name, and no log line names an upload."""
    names = sorted({n for n in (str(sc.get("name") or ""), os.path.basename(str(sc.get("name") or "").replace("\\", "/")),
                                _plain_name(sc, pair), pair, safe_name(pair)) if len(n) > 2}, key=len, reverse=True)
    stand_in = f"upload {upload_id_of(pair) or 'file'}"

    def say(*a: Any) -> None:
        text = " ".join(str(x) for x in a)
        for n in names:
            text = text.replace(n, stand_in)
        log(text)
    return say


def _server_pair(store, src: str, dest: str, sc: dict, *, ledger, deadline, log) -> dict:
    pair = os.path.basename(src)
    log = _no_names(log, sc, pair)
    gz = bool(sc.get("gz"))
    try:
        h = hash_file(src, gz)
    except ValueError as exc:
        os.replace(src, dest)
        return _result("refused", [f"Not read: {exc} Upload it again."])
    if h.sha256() != sc["sha256"] or (sc.get("size") is not None and int(sc["size"]) != h.size):
        os.replace(src, dest)
        return _result("refused", ["Not read: the file changed on the way (it does not match what the page sent). Upload it "
                                   "again."])
    os.replace(src, dest)            # the kept copy: a held file, a re-read or the clean-up of a misread file reads it here
    origin = {"path": dest, "upload_id": upload_id_of(pair), "pair": pair, "user": sc.get("user") or "", "name": sc.get("name") or "",
              "size": h.size, "content_hash": h.content_hash()}
    work = os.path.dirname(dest)     # a file that is no report is read under its own name in done/read-<upload id>/
    try:
        res = read_file(store, dest, name=_plain_name(sc, pair), origin=origin, file_id=h.sha256(), ledger=ledger,
                        deadline=deadline, work_dir=work, log=log)
    except Exception as exc:       # noqa: BLE001  (never the message: a converter's message can hold a cell)
        log(f"  upload {upload_id_of(pair)}: not read ({type(exc).__name__})")
        return _result("refused", [f"Not read: the file could not be read ({type(exc).__name__}). Upload it again, or "
                                   "export it again from EMA."])
    _supersede_twins(store, h, h.sha256())
    _fill_origin(store.conn, h.sha256(), origin)
    _mark_uploads_row(store.conn, h.sha256(), dest)
    return _summarise(res)


def _close_pair(data_dir: str, pair: str, sc: dict, result: dict) -> None:
    """The sidecar, with the result, goes to done/ beside the file; the one in ready/ goes last."""
    body = {k: sc.get(k) for k in SIDECAR_KEYS}
    body["result"] = result
    _write_private(os.path.join(done_dir(data_dir), pair + ".json"), json.dumps(body, ensure_ascii=False).encode("utf-8"))
    try:
        os.remove(os.path.join(ready_dir(data_dir), pair + ".json"))
    except OSError:
        pass


def _finish_orphans(store, data_dir: str, *, ledger, log, out: dict) -> None:
    """A cycle that stopped between moving a file to done/ and writing its result leaves the sidecar alone in ready/:
    the file in done/ is read again (it is 'already' when it was read) and the pair is closed."""
    folder = ready_dir(data_dir)
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for f in names:
        if not f.endswith(".json") or f.startswith(".") or os.path.exists(os.path.join(folder, f[:-5])):
            continue
        pair = f[:-5]
        kept = os.path.join(done_dir(data_dir), pair)
        if not os.path.isfile(kept) or os.path.exists(kept + ".json"):
            continue
        try:
            with open(os.path.join(folder, f), "rb") as fh:
                sc = read_sidecar(fh.read())
        except OSError:
            sc = None
        if sc is None:
            continue
        origin = {"path": kept, "upload_id": upload_id_of(pair), "pair": pair, "user": sc.get("user") or "", "name": sc.get("name") or "",
                  "size": sc.get("size")}
        try:
            res = _summarise(read_file(store, kept, name=_plain_name(sc, pair), origin=origin, file_id=sc["sha256"], ledger=ledger,
                                       work_dir=done_dir(data_dir), log=log))
        except Exception as exc:       # noqa: BLE001
            res = _result("refused", [f"Not read ({type(exc).__name__})."])
        _close_pair(data_dir, pair, sc, res)
        out["pairs"] += 1
        out["read"] += 1


def kick(data_dir: str | None = None) -> bool:
    """An upload has finished on a server: ask for a cycle soon (it reads the intake folder first, under the cycle's
    lock). The scheduler in the web process runs it after its short wait, or after the cycle already going; without a
    running scheduler, a cycle is started in the background. Returns whether a cycle was asked for."""
    from . import pipeline
    try:
        if pipeline.SCHEDULER.is_alive():
            pipeline.SCHEDULER.poke("upload")
            return True
        db = pipeline.SCHEDULER.db_path or os.environ.get("REMITWATCH_DB") or (
            os.path.join(data_dir, "remitwatch.sqlite") if data_dir else "")
        if not db or not os.path.isfile(db):
            return False            # never a cycle on a store that is not there (it would make an empty one)
        pipeline.run_once_in_background(db, trigger="upload")
        return True
    except Exception:       # noqa: BLE001  (the next cycle reads the pair anyway)
        return False


# --------------------------------------------------------------------------------------------------------- serverless
def state_pending(state) -> list[dict]:
    """The complete pairs waiting in the state folder's intake/ (each sidecar with 'pair' and the file's stored size),
    oldest first. done/ is not looked into."""
    try:
        listing = state.list(STATE_INTAKE)
    except Exception:       # noqa: BLE001  (no intake folder yet)
        return []
    files = {m["name"]: m for m in listing}
    out = []
    prefix = STATE_INTAKE + "/"
    for name, m in files.items():
        rest = name[len(prefix):] if name.startswith(prefix) else None
        if not rest or "/" in rest or not rest.endswith(".json") or rest.startswith("."):
            continue
        data_name = name[:-5]
        if data_name not in files:
            continue
        sc = read_sidecar(state.get(name))
        if sc is not None:
            out.append(dict(sc, pair=rest[:-5], stored=files[data_name].get("size")))
    return sorted(out, key=lambda s: (str(s.get("received_at") or ""), s["pair"]))


def process_state(store, state, *, production: Production | None = None, ledger=None, deadline: float | None = None,
                  max_bytes: int | None = None, tmp_dir: str | None = None, defer_close: bool = False, log=print) -> dict:
    """Read every complete pair in the state folder's intake/ (a serverless host), oldest first, before the run looks
    at Dropbox: download the file to scratch space, checking the SHA-256 of its original bytes as it arrives (a .gz is
    unpacked as it is read), read it with origin 'upload' (a Production report through `production`, the run's slot),
    then move the pair to intake/done/ with the result in its sidecar. With `defer_close` the moves wait in
    out['to_close'] for close_state_pairs(), which the run calls once its store image is saved: a run stopped before
    that leaves the pair in intake/ and the next run reads it again ('already' when it was applied). A file larger than
    `max_bytes` as stored is moved to done/ with a plain message. Returns counts as process_ready does."""
    from . import runtime
    out: dict = {"pairs": 0, "read": 0, "refused": 0, "changed": False, "results": []}
    todo = state_pending(state)
    if not todo:
        return out
    base = os.path.join(tmp_dir or runtime.tmp_dir(), "intake")
    for sc in todo:
        if deadline is not None and time.time() > deadline:
            out["remaining"] = out.get("remaining", 0) + 1
            continue
        pair = sc["pair"]
        out["pairs"] += 1
        work = os.path.join(base, upload_id_of(pair) or "pair")
        shutil.rmtree(work, ignore_errors=True)
        os.makedirs(work, exist_ok=True)
        try:
            res = _state_pair(store, state, pair, sc, work, production=production, ledger=ledger, deadline=deadline,
                              max_bytes=max_bytes, log=log)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        if res.get("status") == "later":            # the run's time or its Production slot ran out: next run
            out["pairs"] -= 1
            out["remaining"] = out.get("remaining", 0) + 1
            continue
        if defer_close:
            out.setdefault("to_close", []).append((pair, sc, res))
        else:
            _close_state_pair(state, pair, sc, res)
        out["results"].append({"pair": pair, **{k: res.get(k) for k in ("status", "report", "file_id")}})
        if res["status"] in ("refused", "not_read", "failed"):
            out["refused"] += 1
        else:
            out["read"] += 1
        if res["status"] not in ("already",):
            out["changed"] = True
    return out


def _state_pair(store, state, pair: str, sc: dict, work: str, *, production, ledger, deadline, max_bytes, log) -> dict:
    log = _no_names(log, sc, pair)
    gz = bool(sc.get("gz"))
    stored = sc.get("stored")
    if max_bytes is not None and stored is not None and int(stored) > max_bytes:
        return _result("too_large", [f"Too big to read on this site ({int(stored) >> 20} MB as sent, more than "
                                     f"{max_bytes >> 20} MB): let the server read it, or export a shorter date range."])
    local = os.path.join(work, safe_name(pair) or "file")
    chunks = state.get_chunks(f"{STATE_INTAKE}/{pair}")
    if chunks is None:
        return _result("refused", ["Not read: the file is gone from the intake folder. Upload it again."])
    h = Hashes()
    unpack = zlib.decompressobj(31) if gz else None
    try:
        with open(local, "wb") as fh:
            for c in chunks:
                fh.write(c)
                h.update(unpack.decompress(c) if unpack is not None else c)
            if unpack is not None:
                h.update(unpack.flush())
    except zlib.error:
        return _result("refused", ["Not read: the file could not be unpacked. Upload it again."])
    if h.sha256() != sc["sha256"] or (sc.get("size") is not None and int(sc["size"]) != h.size):
        return _result("refused", ["Not read: the file changed on the way (it does not match what the page sent). Upload it "
                                   "again."])
    origin = {"state_path": f"{STATE_INTAKE}/{DONE}/{pair}", "upload_id": upload_id_of(pair), "pair": pair, "user": sc.get("user") or "",
              "name": sc.get("name") or "", "size": h.size, "content_hash": h.content_hash()}
    try:
        res = read_file(store, local, name=_plain_name(sc, pair), origin=origin, file_id=h.sha256(), ledger=ledger,
                        production=production, deadline=deadline, work_dir=work, log=log)
    except Exception as exc:       # noqa: BLE001  (never the message: a converter's message can hold a cell)
        log(f"  upload {upload_id_of(pair)}: not read ({type(exc).__name__})")
        return _result("refused", [f"Not read: the file could not be read ({type(exc).__name__}). Upload it again, or "
                                   "export it again from EMA."])
    if res.get("status") == "later":
        return res
    _supersede_twins(store, h, h.sha256())
    _fill_origin(store.conn, h.sha256(), origin)
    _mark_uploads_row(store.conn, h.sha256(), None)
    return _summarise(res)


def _close_state_pair(state, pair: str, sc: dict, result: dict) -> None:
    """The file goes to intake/done/, then its sidecar there with the result, then the sidecar in intake/ goes. A run
    that stops between leaves the pair to be read again ('already') by the next."""
    body = {k: sc.get(k) for k in SIDECAR_KEYS}
    body["result"] = result
    state.move(f"{STATE_INTAKE}/{pair}", f"{STATE_INTAKE}/{DONE}/{pair}")
    state.put(f"{STATE_INTAKE}/{DONE}/{pair}.json", json.dumps(body, ensure_ascii=False).encode("utf-8"))
    state.delete(f"{STATE_INTAKE}/{pair}.json")


def close_state_pairs(state, to_close: Iterable[tuple]) -> int:
    """Move the pairs a run read to intake/done/ with their results (after the run saved its store image)."""
    n = 0
    for pair, sc, res in to_close or []:
        _close_state_pair(state, pair, sc, res)
        n += 1
    return n


def later(reason: str) -> dict:
    """What a Production slot returns for a file the run cannot read now (its time or its one Production file is used):
    the pair stays in intake/ for the next run."""
    return {"status": "later", "reason": reason}
