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

from . import runlog

INCOMING, READY, DONE = "incoming", "ready", "done"
STATE_INTAKE = "intake"
SIDECAR_KEYS = ("name", "origin", "user", "sha256", "size", "gz", "received_at", "report_hint")
BLOCK = 4 * 1024 * 1024             # Dropbox's content hash works in blocks of 4 MiB
MAX_LINES = 12
# One file must never stop every cycle: each pair's reading is noted before it starts (server: the store's meta; serverless:
# a small file in the state folder, written before the run reads a byte) and the note goes once the reading returns. A
# pair whose reading started PAIR_TRIES times and never returned (the cycle died in it: the memory limit, the time limit)
# is set aside without being read: held on the Data tab with what happened, Apply or Discard there.
PAIR_TRIES = 2
ATTEMPTS_META = "intake:attempts"           # server
STATE_ATTEMPTS = "intake_attempts.json"     # serverless (beside the state folder's other files, not in intake/)
SET_ASIDE = ("Set aside without being read: reading it stopped the cycle {n} times (a damaged file, or one far larger "
             "than it looks once unpacked), so the rest of the work went on without it. Export it again from EMA and upload "
             "it again, or choose Apply on the Data tab to try it once more.")


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


def hash_file(path: str, gz: bool, *, expect: int | None = None) -> Hashes:
    """The hashes of a file's original bytes: a .gz is unpacked as it is read (never whole), never to more than
    ema_reports.unpack_limit of its size (a gzip bomb raises the unpacks_too_large refusal, a ReportRefused), and no
    further than one byte past `expect` (the sidecar's size: a file that unpacks to more is not the one that was sent;
    the caller sees the size differ). Raises ValueError for a file that says it is gzip and is not."""
    from . import ema_reports as er
    h = Hashes()
    try:
        if gz:
            packed = os.path.getsize(path)
            with gzip.open(path, "rb") as fh:
                src = er.Capped(fh, er.unpack_limit(packed), packed=packed)
                for piece in iter(lambda: src.read(1 << 20), b""):
                    h.update(piece)
                    if expect is not None and h.size > expect:
                        break
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
        if r.get("remits") or r.get("ema") or r.get("claims_837"):
            # read, in whole or in part (a zip whose damaged member is named among the lines)
            return _result("read", res.get("lines") or ["Read."])
        if r.get("failed"):
            return _result("refused", res.get("lines") or ["Not read."])
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
        words: list[str] = []
        try:
            from .uploads import describe      # the one-piece route's words: the report, its visits, rows and claims
            for f in r.get("ema_files") or []:
                words += describe(f, "done")
        except Exception:                       # noqa: BLE001  (the words only; what was read is already stored)
            words = []
        lines += words or ["Read as an EMA export."]
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
        # what is no report is unpacked to be read under its own name, never past what such a file can be (a weekly
        # export, remittances): one that unpacks to more is set aside whole, never read cut short
        n = 0
        limit = min(er.unpack_limit(os.path.getsize(path)), NOT_REPORT_UNPACK_MAX())
        try:
            with gzip.open(path, "rb") as src, open(target, "wb") as dst:
                for piece in iter(lambda: src.read(1 << 20), b""):
                    n += len(piece)
                    if n > limit:
                        raise er.unpack_refusal(limit, os.path.getsize(path))
                    dst.write(piece)
        except er.ReportRefused:
            _remove_quietly(target)
            raise
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


def NOT_REPORT_UNPACK_MAX() -> int:      # noqa: N802  (a limit that depends on the host)
    """The most a gzipped upload that is none of EMA's reports may unpack to: a server reads what such files can be
    (weekly exports, remittances, a zip of them); a serverless host's scratch space is 512 MB and holds the ledger too."""
    from . import runtime
    return (100 << 20) if runtime.serverless() else (400 << 20)


def _remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


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


def _meta_attempts(conn) -> dict:
    try:
        r = conn.execute("SELECT value FROM meta WHERE key=?", (ATTEMPTS_META,)).fetchone()
        d = json.loads(r[0]) if r and r[0] else {}
    except (ValueError, TypeError, Exception):      # noqa: BLE001  (no meta table yet: nothing noted)
        d = {}
    return d if isinstance(d, dict) else {}


def note_server_attempt(conn, pair: str) -> int:
    """Note on the server (the store's meta, committed at once) that a pair's reading starts; returns how many times it
    has started, this one included."""
    d = _meta_attempts(conn)
    n = int((d.get(pair) or {}).get("n") or 0) + 1
    d[pair] = {"n": n, "at": _now()}
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (ATTEMPTS_META, json.dumps(d, sort_keys=True)))
    conn.commit()
    return n


def clear_server_attempt(conn, pair: str) -> None:
    d = _meta_attempts(conn)
    if d.pop(pair, None) is None:
        return
    if d:
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (ATTEMPTS_META, json.dumps(d, sort_keys=True)))
    else:
        conn.execute("DELETE FROM meta WHERE key=?", (ATTEMPTS_META,))
    conn.commit()


def _set_aside(store, sc: dict, pair: str, n: int, origin: dict) -> dict:
    """A pair whose reading stopped the cycle PAIR_TRIES times: held in the report catalog under its SHA-256 (the same
    file from Dropbox is then not read either), with what happened and Apply / Discard on the Data tab. A file the catalog
    already knows (applied by a run that died at its saves, refused, held) keeps that answer."""
    from . import ema_reports as er
    fid = sc.get("sha256") or ""
    try:
        row = er._catalog_row(store.conn, fid) if fid else None
    except Exception:       # noqa: BLE001  (no catalog yet)
        row = None
    if row and row.get("status") in er.DONE_STATUSES + ("refused", "discarded", "held"):
        st = row["status"]
        return _result("already" if st in er.DONE_STATUSES else st,
                       [f"Already read on an earlier run ({st})."] if st in er.DONE_STATUSES
                       else [str(row.get("status_reason") or "Not read.")], file_id=fid, report=row.get("report"))
    msg = SET_ASIDE.format(n=n - 1)
    if fid:
        try:
            er.ensure_schema(store.conn)
            er._catalog_put(store.conn, fid, commit=True, report=None, source_name=_plain_name(sc, pair), via="upload",
                            origin=origin, source_kind="upload", status="held", status_reason=msg, received_at=_now())
            store.ema_changed = True
        except Exception as exc:       # noqa: BLE001  (the done sidecar still says what happened)
            runlog.caught(exc, "intake_set_aside", level="warning")
    return _result("held", [msg], file_id=fid)


def process_ready(store, data_dir: str | None = None, *, ledger=None, deadline: float | None = None, log=print) -> dict:
    """Read every complete pair in <data_dir>/incoming/ready/ (a server), oldest first: check its bytes against the
    sidecar, move the file to done/, read it from there (its kept copy) with origin 'upload', and write the sidecar to
    done/ with the result. `ledger` is the cycle's ledger opener (a Production report is applied with it). Each pair's
    reading is noted before it starts; one whose reading stopped the cycle PAIR_TRIES times is set aside unread (held).
    Returns counts: pairs, read, refused, changed (whether anything new reached the store), and per pair its result."""
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
        n = note_server_attempt(store.conn, pair)        # before a byte of it is read
        if n > PAIR_TRIES:
            dest = os.path.join(done, pair)
            os.replace(src, dest)
            res = _set_aside(store, sc, pair, n, {"path": dest, "upload_id": upload_id_of(pair), "pair": pair,
                                                  "user": sc.get("user") or "", "name": sc.get("name") or "", "size": sc.get("size")})
            log(f"  upload {upload_id_of(pair) or 'file'}: set aside after {n - 1} cycles that stopped while reading it")
        else:
            res = _server_pair(store, src, os.path.join(done, pair), sc, ledger=ledger, deadline=deadline, log=log)
        _close_pair(data_dir, pair, sc, res)
        clear_server_attempt(store.conn, pair)
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


def _bomb_result(store, exc, sc: dict, pair: str, origin: dict) -> dict:
    """A pair that unpacks to far more than any export (or holds a line longer than any export has): refused in the report
    catalog under its SHA-256 (the same file from Dropbox is not read either), and listed on the Data tab with why."""
    from . import ema_reports as er
    fid = sc.get("sha256") or ""
    if fid:
        try:
            er._record_refusal(store, fid, exc, path=_plain_name(sc, pair), rec=None, via="upload", origin=origin,
                               log=lambda *a: None)
        except Exception as err:       # noqa: BLE001  (the done sidecar still says what happened)
            runlog.caught(err, "intake_refused", level="warning")
    return _result("refused", [f"Not read: {exc.message}"], file_id=fid or None)


def _server_pair(store, src: str, dest: str, sc: dict, *, ledger, deadline, log) -> dict:
    from . import ema_reports as er
    pair = os.path.basename(src)
    log = _no_names(log, sc, pair)
    gz = bool(sc.get("gz"))
    try:
        h = hash_file(src, gz, expect=int(sc["size"]) if sc.get("size") is not None else None)
    except er.ReportRefused as exc:     # a gzip bomb: never unpacked past the limit
        os.replace(src, dest)
        log(f"  upload {upload_id_of(pair) or 'file'}: set aside ({exc.code})")
        return _bomb_result(store, exc, sc, pair, {"path": dest, "upload_id": upload_id_of(pair), "pair": pair,
                                                   "user": sc.get("user") or "", "name": sc.get("name") or ""})
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
    except er.ReportRefused as exc:     # it unpacks to more than such a file can be: set aside whole, never read cut short
        log(f"  upload {upload_id_of(pair)}: set aside ({exc.code})")
        return _bomb_result(store, exc, sc, pair, origin)
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
        n = note_server_attempt(store.conn, pair)
        if n > PAIR_TRIES:
            res = _set_aside(store, sc, pair, n, origin)
        else:
            from . import ema_reports as er
            try:
                res = _summarise(read_file(store, kept, name=_plain_name(sc, pair), origin=origin, file_id=sc["sha256"], ledger=ledger,
                                           work_dir=done_dir(data_dir), log=_no_names(log, sc, pair)))
            except er.ReportRefused as exc:
                res = _bomb_result(store, exc, sc, pair, origin)
            except Exception as exc:       # noqa: BLE001
                res = _result("refused", [f"Not read ({type(exc).__name__})."])
        _close_pair(data_dir, pair, sc, res)
        clear_server_attempt(store.conn, pair)
        out["pairs"] += 1
        out["read"] += 1


# ------------------------------------------------------------------------------------------------- done/ retention
DONE_KEEP_DAYS = 180            # a read upload's kept copy and its sidecar are removed this long after they arrived...
KEEP_STATUSES = ("held", "waiting", "reread", "current", "partly_replaced")      # ... never while its file is one of these


def _days_since(iso: Any, now: float) -> float | None:
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (now - t.timestamp()) / 86400


def _kept_copies(conn) -> tuple[set, set]:
    """What retention never removes: the file ids and kept-copy paths (server 'path', serverless 'state_path') of every
    report the catalog holds as held, waiting, to be read again or still live, and of the newest file of each report."""
    ids: set = set()
    paths: set = set()
    try:
        rows = conn.execute("SELECT file_id, report, status, received_at, origin FROM ema_report_files").fetchall()
    except Exception:       # noqa: BLE001  (no catalog yet: nothing is a report's copy)
        return ids, paths
    newest: dict = {}
    for fid, report, status, received, origin in rows:
        try:
            o = json.loads(origin) if origin else {}
        except ValueError:
            o = {}
        where = o.get("path") or o.get("state_path")
        if status in KEEP_STATUSES:
            ids.add(fid)
            if where:
                paths.add(where)
        if where and report and status not in ("refused", "discarded") and str(received or "") >= str((newest.get(report) or ("",))[0]):
            newest[report] = (str(received or ""), fid, where)
    for _r, fid, where in newest.values():
        ids.add(fid)
        paths.add(where)
    return ids, paths


def retain_done_server(store, data_dir: str | None = None, *, now: float | None = None, log=print) -> dict:
    """A server's read uploads (<data_dir>/incoming/done/: each data file, its sidecar with the result, and the folder a
    file that is no report was read from) are removed DONE_KEEP_DAYS after they arrived, never the copy of a report held,
    waiting or still live, nor the newest file of each report. The catalog row and the files row stay (the Upload page
    then says when it was read). Returns counts."""
    data_dir = data_dir or os.path.dirname(os.path.abspath(store.db_path))
    folder = done_dir(data_dir)
    now = now or time.time()
    out = {"removed": 0, "kept": 0}
    try:
        names = os.listdir(folder)
    except OSError:
        return out
    ids, paths = _kept_copies(store.conn)
    for f in names:
        if not f.endswith(".json") or f.startswith("."):
            continue
        pair = f[:-5]
        data = os.path.join(folder, pair)
        try:
            with open(os.path.join(folder, f), "rb") as fh:
                sc = read_sidecar(fh.read()) or {}
        except OSError:
            sc = {}
        age = _days_since(sc.get("received_at"), now)
        if age is None:
            try:
                age = (now - os.path.getmtime(os.path.join(folder, f))) / 86400
            except OSError:
                continue
        if age <= DONE_KEEP_DAYS:
            continue
        if sc.get("sha256") in ids or data in paths or os.path.abspath(data) in paths:
            out["kept"] += 1
            continue
        for p in (data, os.path.join(folder, f)):
            _remove_quietly(p)
        shutil.rmtree(os.path.join(folder, "read-" + (upload_id_of(pair) or "x")), ignore_errors=True)
        out["removed"] += 1
    if out["removed"]:
        log(f"  {out['removed']} read upload(s) older than {DONE_KEEP_DAYS} days removed from the intake folder")
    return out


def retain_done_state(store, state, *, now: float | None = None, log=print) -> dict:
    """The same on a serverless host, for the state folder's intake/done/ (ages from the folder's own times: when each
    pair was moved there, that is, read)."""
    now = now or time.time()
    out = {"removed": 0, "kept": 0}
    try:
        listing = state.list(f"{STATE_INTAKE}/{DONE}")
    except Exception:       # noqa: BLE001  (no done folder yet)
        return out
    ids, paths = _kept_copies(store.conn)
    prefix = f"{STATE_INTAKE}/{DONE}/"
    by_name = {m["name"]: m for m in listing if str(m.get("name") or "").startswith(prefix)}
    gone: list[str] = []
    for name, m in by_name.items():
        rest = name[len(prefix):]
        if not rest or "/" in rest or rest.endswith(".json") or rest.startswith("."):
            continue
        age = _days_since(m.get("modified"), now)
        if age is None or age <= DONE_KEEP_DAYS:
            continue
        if name in paths:
            out["kept"] += 1
            continue
        gone += [name] + ([name + ".json"] if name + ".json" in by_name else [])
        out["removed"] += 1
    for name, m in by_name.items():          # a sidecar whose data file went before (an older clean-up of a copy)
        if name.endswith(".json") and name[:-5] not in by_name and name not in gone:
            age = _days_since(m.get("modified"), now)
            if age is not None and age > DONE_KEEP_DAYS and name[:-5] not in paths:
                gone.append(name)
    if gone:
        try:
            state.delete_many(gone)
        except Exception as exc:       # noqa: BLE001  (tried again by a later run)
            log(f"  intake clean-up could not finish ({type(exc).__name__})")
            return dict(out, error=type(exc).__name__)
        log(f"  {out['removed']} read upload(s) older than {DONE_KEEP_DAYS} days removed from the intake folder")
    return out


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
        # the reading is noted in the state folder before a byte is read: a run the platform stops in it (its memory or
        # time limit) still counts, and after PAIR_TRIES such runs the pair is set aside unread
        n = note_state_attempt(state, pair)
        if n > PAIR_TRIES:
            res = _set_aside(store, sc, pair, n, {"state_path": f"{STATE_INTAKE}/{DONE}/{pair}", "upload_id": upload_id_of(pair),
                                                  "pair": pair, "user": sc.get("user") or "", "name": sc.get("name") or "",
                                                  "size": sc.get("size"), "stored": sc.get("stored")})
            log(f"  upload {upload_id_of(pair) or 'file'}: set aside after {n - 1} runs that stopped while reading it")
        else:
            work = os.path.join(base, upload_id_of(pair) or "pair")
            shutil.rmtree(work, ignore_errors=True)
            os.makedirs(work, exist_ok=True)
            handed: list = []

            def slot(*a, _pair=pair, _handed=handed, **kw):
                # the run's Production slot counts its own attempts from here (three, then the file is held): the pair's
                # note goes, so the two never count one stopped run twice
                if not _handed:
                    clear_state_attempt(state, _pair)
                    _handed.append(True)
                return production(*a, **kw)
            try:
                res = _state_pair(store, state, pair, sc, work, production=slot if production is not None else None,
                                  ledger=ledger, deadline=deadline, max_bytes=max_bytes, log=log)
            finally:
                shutil.rmtree(work, ignore_errors=True)
        if n <= PAIR_TRIES:
            clear_state_attempt(state, pair)        # the reading returned: whatever comes after is not this file's doing
        # (a pair set aside keeps its note until it is moved to intake/done/: close_state_pairs clears it)
        if res.get("status") == "later":            # the run's time or its Production slot ran out: next run
            out["pairs"] -= 1
            out["remaining"] = out.get("remaining", 0) + 1
            continue
        if defer_close:
            out.setdefault("to_close", []).append((pair, sc, res))
        else:
            _close_state_pair(state, pair, sc, res)
            clear_state_attempt(state, pair)
        out["results"].append({"pair": pair, **{k: res.get(k) for k in ("status", "report", "file_id")}})
        if res["status"] in ("refused", "not_read", "failed"):
            out["refused"] += 1
        else:
            out["read"] += 1
        if res["status"] not in ("already",):
            out["changed"] = True
    return out


def _state_attempts(state) -> dict:
    try:
        d = json.loads(state.get(STATE_ATTEMPTS) or b"{}")
    except (ValueError, TypeError):
        d = {}
    return d if isinstance(d, dict) else {}


def note_state_attempt(state, pair: str) -> int:
    """Note in the state folder (written at once) that a pair's reading starts; returns how many times it has started,
    this one included. A state folder that cannot be written reads the pair all the same (returns 1)."""
    d = _state_attempts(state)
    n = int((d.get(pair) or {}).get("n") or 0) + 1
    d[pair] = {"n": n, "at": _now()}
    try:
        state.put(STATE_ATTEMPTS, json.dumps(d, sort_keys=True).encode())
    except Exception as exc:       # noqa: BLE001  (the note is a safeguard; the reading goes on)
        runlog.caught(exc, "intake_attempts", level="warning")
        return 1
    return n


def clear_state_attempt(state, *pairs: str) -> None:
    d = _state_attempts(state)
    if not any(p in d for p in pairs):
        return
    for p in pairs:
        d.pop(p, None)
    try:
        if d:
            state.put(STATE_ATTEMPTS, json.dumps(d, sort_keys=True).encode())
        else:
            state.delete(STATE_ATTEMPTS)
    except Exception as exc:       # noqa: BLE001  (an old note only lets a later run count one stop too many)
        runlog.caught(exc, "intake_attempts", level="warning")


def _unpacked_into(chunks: Iterable[bytes], fh, h: Hashes, gz: bool, *, expect: int | None, packed: int | None) -> None:
    """Write the stored bytes to `fh` and feed the original bytes to `h`: a .gz is unpacked 8 MB at a time, never past
    ema_reports.unpack_limit of its stored size (a gzip bomb raises the unpacks_too_large refusal) and no further than one
    byte past `expect` (the sidecar's size: the size then differs and the pair is refused)."""
    from . import ema_reports as er
    unpack = zlib.decompressobj(31) if gz else None
    limit = er.unpack_limit(packed)
    for c in chunks:
        fh.write(c)
        if unpack is None:
            h.update(c)
            continue
        buf = c
        while buf and not unpack.eof:
            h.update(unpack.decompress(buf, 8 << 20))
            if h.size > limit:
                raise er.unpack_refusal(limit, packed)
            if expect is not None and h.size > expect:
                return
            buf = unpack.unconsumed_tail
    if unpack is not None and not unpack.eof:
        h.update(unpack.flush())


def _state_pair(store, state, pair: str, sc: dict, work: str, *, production, ledger, deadline, max_bytes, log) -> dict:
    from . import ema_reports as er
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
    origin = {"state_path": f"{STATE_INTAKE}/{DONE}/{pair}", "upload_id": upload_id_of(pair), "pair": pair, "user": sc.get("user") or "",
              "name": sc.get("name") or ""}
    try:
        with open(local, "wb") as fh:
            _unpacked_into(chunks, fh, h, gz, expect=int(sc["size"]) if sc.get("size") is not None else None,
                           packed=int(stored) if stored is not None else None)
    except zlib.error:
        return _result("refused", ["Not read: the file could not be unpacked. Upload it again."])
    except er.ReportRefused as exc:     # a gzip bomb: never unpacked past the limit
        log(f"  upload {upload_id_of(pair) or 'file'}: set aside ({exc.code})")
        return _bomb_result(store, exc, sc, pair, dict(origin, size=sc.get("size")))
    if h.sha256() != sc["sha256"] or (sc.get("size") is not None and int(sc["size"]) != h.size):
        return _result("refused", ["Not read: the file changed on the way (it does not match what the page sent). Upload it "
                                   "again."])
    # the size kept with the origin is what the run fetches again (the stored bytes: a .gz is smaller than its original)
    origin.update(size=h.size, stored=os.path.getsize(local), content_hash=h.content_hash())
    try:
        res = read_file(store, local, name=_plain_name(sc, pair), origin=origin, file_id=h.sha256(), ledger=ledger,
                        production=production, deadline=deadline, work_dir=work, log=log)
    except er.ReportRefused as exc:     # it unpacks to more than such a file can be: set aside whole, never read cut short
        log(f"  upload {upload_id_of(pair) or 'file'}: set aside ({exc.code})")
        return _bomb_result(store, exc, sc, pair, origin)
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
    """Move the pairs a run read to intake/done/ with their results (after the run saved its store image), and drop
    their attempt notes."""
    n = 0
    closed = []
    for pair, sc, res in to_close or []:
        _close_state_pair(state, pair, sc, res)
        closed.append(pair)
        n += 1
    if closed:
        clear_state_attempt(state, *closed)
    return n


def later(reason: str) -> dict:
    """What a Production slot returns for a file the run cannot read now (its time or its one Production file is used):
    the pair stays in intake/ for the next run."""
    return {"status": "later", "reason": reason}
